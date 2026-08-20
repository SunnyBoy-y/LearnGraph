//! Desktop Supervisor — owns the Windows child processes (api + preview),
//! waits for health, then navigates the main window to the API origin.
//!
//! Phase 1 scope (see doc/Windows_EXE_桌面化_Phase0_测量报告_v1.0.md):
//!   - dynamic loopback ports
//!   - sidecar spawn (dev: system python uvicorn; prod: LEARNGRAPH_SERVICE_EXE)
//!   - Job Object with KILL_ON_JOB_CLOSE (process-tree cleanup on app exit)
//!   - HTTP health polling
//!   - navigate main window to http://127.0.0.1:<api-port>/
//!
//! The web page runs on a remote loopback origin and intentionally has ZERO
//! Tauri IPC (see assessment doc §3.5).

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};

/// Windows Job Object that kills every assigned process when the handle
/// closes (i.e. when the app exits). Fallback for children we cannot reach.
struct JobGuard {
    job: windows_sys::Win32::Foundation::HANDLE,
}

impl JobGuard {
    fn new() -> Result<Self, String> {
        unsafe {
            use windows_sys::Win32::Foundation::CloseHandle;
            use windows_sys::Win32::System::JobObjects::*;

            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err("CreateJobObjectW failed".into());
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const _,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                CloseHandle(job);
                return Err("SetInformationJobObject failed".into());
            }
            Ok(Self { job })
        }
    }

    fn assign_pid(&self, pid: u32) -> Result<(), String> {
        unsafe {
            use windows_sys::Win32::Foundation::CloseHandle;
            use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
            use windows_sys::Win32::System::Threading::{
                OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
            };

            let h = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
            if h.is_null() {
                return Err(format!("OpenProcess({pid}) failed"));
            }
            let ok = AssignProcessToJobObject(self.job, h);
            CloseHandle(h);
            if ok == 0 {
                Err(format!("AssignProcessToJobObject({pid}) failed"))
            } else {
                Ok(())
            }
        }
    }
}

impl Drop for JobGuard {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.job);
        }
    }
}

struct SidecarSpec {
    role: &'static str,
    module: &'static str,
}

/// Desktop data layout under %LOCALAPPDATA%\LearnGraph (kept out of the repo).
struct DesktopDirs {
    data: PathBuf,
    storage: PathBuf,
    memory: PathBuf,
    workspaces: PathBuf,
    egress_policies: PathBuf,
    logs: PathBuf,
}

impl DesktopDirs {
    fn ensure() -> Option<Self> {
        let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from)?.join("LearnGraph");
        let dirs = Self {
            data: base.join("data"),
            storage: base.join("storage"),
            memory: base.join("memory"),
            workspaces: base.join("sandbox-workspaces"),
            egress_policies: base.join("egress-policies"),
            logs: base.join("logs"),
        };
        for dir in [
            &dirs.data,
            &dirs.storage,
            &dirs.memory,
            &dirs.workspaces,
            &dirs.egress_policies,
            &dirs.logs,
        ] {
            if std::fs::create_dir_all(dir).is_err() {
                return None;
            }
        }
        Some(dirs)
    }

    /// sqlite:///C:/... absolute URL with forward slashes.
    fn sqlite_url(path: &Path) -> String {
        format!("sqlite:///{}", path.to_string_lossy().replace('\\', "/"))
    }

    fn apply_env(&self, cmd: &mut Command) {
        cmd.env("LEARNGRAPH_DATABASE_URL", Self::sqlite_url(&self.data.join("learngraph.db")));
        cmd.env("LEARNGRAPH_STORAGE_ROOT", &self.storage);
        cmd.env("LEARNGRAPH_MEMORY_ROOT", &self.memory);
        cmd.env("LEARNGRAPH_SANDBOX_WORKSPACE_ROOT", &self.workspaces);
        cmd.env("LEARNGRAPH_SANDBOX_EGRESS_POLICY_DIR", &self.egress_policies);
    }
}

impl SidecarSpec {
    const fn api() -> Self {
        Self { role: "api", module: "app.main:app" }
    }
    const fn preview() -> Self {
        Self { role: "preview", module: "app.preview:preview_app" }
    }
}

fn backend_dir() -> PathBuf {
    if let Ok(d) = std::env::var("LEARNGRAPH_DESKTOP_BACKEND_DIR") {
        if !d.trim().is_empty() {
            return PathBuf::from(d);
        }
    }
    // desktop/src-tauri -> desktop -> repo root -> backend
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(|root| root.join("backend"))
        .unwrap_or_else(|| PathBuf::from("backend"))
}

/// Resolve the dev-mode python interpreter. Priority:
///   LEARNGRAPH_DESKTOP_PYTHON env > backend/.venv/Scripts/python.exe > "python".
fn python_interpreter(backend: &Path) -> std::ffi::OsString {
    if let Ok(p) = std::env::var("LEARNGRAPH_DESKTOP_PYTHON") {
        if !p.trim().is_empty() {
            return p.into();
        }
    }
    let venv = backend.join(".venv").join("Scripts").join("python.exe");
    if venv.is_file() {
        return venv.into_os_string();
    }
    "python".into()
}

fn find_free_port(exclude: u16) -> Result<u16, String> {
    for _ in 0..64 {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).map_err(|e| e.to_string())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        drop(listener);
        if port != exclude {
            return Ok(port);
        }
    }
    Err("no free loopback port found".into())
}

fn spawn_sidecar(
    spec: &SidecarSpec,
    port: u16,
    job: &JobGuard,
    preview_port: Option<u16>,
    dirs: &Option<DesktopDirs>,
) -> Result<Child, String> {
    let backend = backend_dir();
    let mut cmd = if let Ok(exe) = std::env::var("LEARNGRAPH_SERVICE_EXE") {
        if exe.trim().is_empty() {
            return Err("LEARNGRAPH_SERVICE_EXE is empty".into());
        }
        let mut c = Command::new(&exe);
        c.arg("--role").arg(spec.role);
        c
    } else {
        // Dev mode: venv/system python + uvicorn, backend repo as cwd.
        let mut c = Command::new(python_interpreter(&backend));
        c.arg("-m")
            .arg("uvicorn")
            .arg(spec.module)
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string());
        c.current_dir(&backend);
        c
    };

    cmd.env("LEARNGRAPH_DEPLOYMENT_PROFILE", "personal_desktop");
    cmd.env("LEARNGRAPH_DESKTOP_SINGLE_USER", "true"); // 桌面版单用户：禁注册/多账号（决策 #4）
    cmd.env("LEARNGRAPH_SANDBOX_BACKEND", "sandboxd");
    cmd.env("LEARNGRAPH_ENV", "development"); // PoC; release flips to production + single-user
    cmd.env("LEARNGRAPH_DESKTOP_ROLE", spec.role);
    if let Some(d) = dirs {
        d.apply_env(&mut cmd);
    }
    // Serve the built frontend from the API process (desktop shape), and tell
    // the API where the preview origin lives.
    if spec.role == "api" {
        let frontend_dist = backend
            .parent()
            .map(|root| root.join("frontend").join("dist"));
        if let Some(dist) = frontend_dist {
            if dist.is_dir() {
                cmd.env("LEARNGRAPH_FRONTEND_DIST", dist);
            }
        }
        if let Some(pp) = preview_port {
            cmd.env("LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN", format!("http://127.0.0.1:{pp}"));
        }
    }
    // Sidecar logs land in %LOCALAPPDATA%\LearnGraph\logs\<role>.log.
    if let Some(d) = dirs {
        if let Ok(file) = std::fs::File::create(d.logs.join(format!("{}.log", spec.role))) {
            let err = file.try_clone().ok();
            cmd.stdout(Stdio::from(file));
            if let Some(e) = err {
                cmd.stderr(Stdio::from(e));
            } else {
                cmd.stderr(Stdio::null());
            }
        } else {
            cmd.stdout(Stdio::null());
            cmd.stderr(Stdio::null());
        }
    } else {
        cmd.stdout(Stdio::null());
        cmd.stderr(Stdio::null());
    }

    let child = cmd.spawn().map_err(|e| {
        format!(
            "spawn sidecar '{}' failed: {e} (dev mode needs `python -m uvicorn`; prod sets LEARNGRAPH_SERVICE_EXE)",
            spec.role
        )
    })?;
    job.assign_pid(child.id()).map_err(|e| format!("{}: {e}", spec.role))?;
    Ok(child)
}

fn http_get(port: u16, path: &str) -> bool {
    let addr = format!("127.0.0.1:{port}");
    let mut sock = match TcpStream::connect_timeout(
        &addr.parse().expect("valid addr"),
        Duration::from_millis(800),
    ) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let req = format!(
        "GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if sock.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 256];
    let n = match sock.read(&mut buf) {
        Ok(n) => n,
        Err(_) => return false,
    };
    let head = String::from_utf8_lossy(&buf[..n]);
    head.starts_with("HTTP/1.0 200") || head.starts_with("HTTP/1.1 200")
}

fn wait_healthy(port: u16, label: &str, deadline: Duration) -> Result<(), String> {
    let start = Instant::now();
    while start.elapsed() < deadline {
        if http_get(port, "/api/v1/livez") {
            println!("[supervisor] {label} healthy on 127.0.0.1:{port}");
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err(format!("{label} did not become healthy on 127.0.0.1:{port} within {deadline:?}"))
}

/// Entry point, runs on a dedicated thread so the UI can render the placeholder
/// while services boot.
pub fn run(app: AppHandle) -> Result<(), String> {
    let job = JobGuard::new()?;
    let dirs = DesktopDirs::ensure();
    if dirs.is_none() {
        println!("[supervisor] warning: LOCALAPPDATA unavailable; data stays in backend/");
    }

    let api_port = find_free_port(0)?;
    let preview_port = find_free_port(api_port)?;

    let api = spawn_sidecar(&SidecarSpec::api(), api_port, &job, Some(preview_port), &dirs)?;
    let preview = spawn_sidecar(&SidecarSpec::preview(), preview_port, &job, None, &dirs)?;

    wait_healthy(api_port, "api", Duration::from_secs(45))?;
    wait_healthy(preview_port, "preview", Duration::from_secs(45))?;

    // Keep children alive for the app lifetime; the Job Object terminates the
    // whole tree when this process exits.
    std::mem::forget(api);
    std::mem::forget(preview);

    let url = format!("http://127.0.0.1:{api_port}/");
    println!("[supervisor] navigating to {url}");
    let nav_app = app.clone();
    let _ = nav_app.run_on_main_thread(move || {
        if let Some(window) = app.get_webview_window("main") {
            if let Ok(parsed) = url.parse::<tauri::Url>() {
                let _ = window.navigate(parsed);
            }
        }
    });

    // Keep the Job Object (and children) alive for the app lifetime. When this
    // process exits, the OS closes the job handle and KILL_ON_JOB_CLOSE
    // terminates the whole sidecar tree.
    std::mem::forget(job);

    Ok(())
}

# LearnGraph Desktop (Phase 1 脚手架)

Windows 桌面壳：**Tauri 2 + Rust Supervisor + FastAPI sidecar（api/preview 双进程）**。

决策依据：`doc/Windows_EXE_桌面版与内置_Linux_沙箱包装方案评估_v1.1.md`（§0.5 决策记录）。
测量依据：`doc/Windows_EXE_桌面化_Phase0_测量报告_v1.0.md`。

## 目录

```
desktop/
├─ src-tauri/            Tauri 2 应用 + Rust Supervisor
│  ├─ src/main.rs        窗口、单实例、启动 Supervisor 线程
│  ├─ src/supervisor.rs  端口分配、sidecar 进程、Job Object、健康检查、导航
│  ├─ tauri.conf.json
│  └─ capabilities/      主窗口=远程回环 origin，零 Tauri IPC（决策 §3.5）
├─ frontend-dist/        占位启动页（"正在启动…"）；正式构建由 FastAPI 托管真实前端
├─ spec/                 PyInstaller sidecar 打包蓝图
└─ package.json
```

## 开发运行（Phase 1 PoC）

前置：本机 Python（含 backend 依赖）、Node、Rust、Tauri 依赖。

```powershell
# 1) 准备后端依赖（首次）
cd backend && uv sync --frozen   # 或 pip install -e .
# 2) 准备前端（桌面版由后端托管 dist，先构建一次）
cd frontend && npm ci && npm run build
# 3) 启动桌面壳（Supervisor 自动拉起 api + preview 两个 uvicorn 进程）
cd desktop && cargo run --manifest-path src-tauri/Cargo.toml
```

Supervisor 行为：

- 为 api / preview 各分配一个随机回环端口；
- 开发态用 `python -m uvicorn app.main:app`（cwd=backend/）启动；
- 生产态读取 `LEARNGRAPH_SERVICE_EXE`（PyInstaller onedir 可执行文件）+ `--role api|preview`；
- 所有子进程加入 Job Object（KILL_ON_JOB_CLOSE），应用退出即整树终止；
- 轮询 `/api/v1/livez` 就绪后导航主窗口到 `http://127.0.0.1:<api-port>/`；
- 沙箱后端默认 `sandboxd`；Phase 1 不启动 daemon，沙箱状态页显示"不可达"（fail-closed，符合预期）。

## 环境变量

| 变量 | 作用 |
|---|---|
| `LEARNGRAPH_SERVICE_EXE` | 生产 sidecar 可执行文件路径（设置后不再用 python uvicorn） |
| `LEARNGRAPH_DESKTOP_BACKEND_DIR` | 覆盖 backend 目录（默认 `../backend`） |

## Phase 1 已知 TODO（不在本脚手架内）

- LocalAppData 重定位（data/storage/memory/logs 移出仓库目录）；
- 单用户模式收敛（禁注册/多账号）；
- WebUI 开关 + access token（决策 §3.6）；
- 动态端口竞态窗口（预占端口在 spawn 前释放，正式版改由 sidecar 回报端口）；
- sidecar 日志落盘与轮转；
- WSL guest sandboxd（Phase 3）。

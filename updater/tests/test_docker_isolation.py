"""No real daemon calls; hostile definitions must fail before any write/exec."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

from updater.agent import Agent
from updater.tests.docker_isolation import OWNER, MARKER, IsolatedAgent, IsolationError, initialize_fixture, validate_fixture, validate_location
from updater.tests.shell_docker_gate import dispatch, backup_helper, metadata_format
from updater.tests.ci_docker_isolation import require_fresh_daemon


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = "lg-pg-maintenance-0123456789"
        self.image = "learngraph-maintenance-fixture-0123456789"
        self.marker = initialize_fixture(self.root, self.project, self.image, {
            "name": self.project, "services": {"app": {"image": self.image + ":v1.0.0", "environment":{"LEARNGRAPH_DATABASE_URL":"sqlite:////data/learngraph.db"}, "volumes": ["data:/data"], "ports": ["127.0.0.1:18247:8000"]}}, "volumes": {"data": {}}}, port=18247)

    def change(self, edit, file="compose.json"):
        p = self.root / file
        config = json.loads(p.read_text())
        edit(config)
        p.write_text(json.dumps(config))

    def agent(self):
        with patch.object(Agent, "command", return_value=""):
            return IsolatedAgent(self.root, "LearnGraph/UpdateFixture", self.image, ["compose.json"])

    def test_missing_marker_refuses_before_agent_creates_token(self):
        (self.root / MARKER).unlink()
        with patch.object(Agent, "command") as docker, self.assertRaises(IsolationError):
            IsolatedAgent(self.root, "LearnGraph/UpdateFixture", self.image)
        docker.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_production_data_roots_rejected_before_mkdir_or_docker(self):
        repo = Path(__file__).resolve().parents[2]
        for root in [repo,repo / "data/never-created",repo / "backend/data/never-created",Path("/data/never-created"),Path("/var/lib/docker/volumes/never-created")]:
            with self.subTest(root=root), self.assertRaises(IsolationError): validate_location(root)

    def test_production_project_and_forged_prefix_refused(self):
        for name in ["learngraph", "lg-pg-maintenance-production"]:
            with self.subTest(name=name):
                self.change(lambda c: c.update(project=name), MARKER)
                with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_copied_marker_cannot_adopt_other_root(self):
        self.change(lambda c: c.update(root=str(self.root / "other")), MARKER)
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    @unittest.skipIf(os.name == "nt", "Symlink check runs in isolated Linux Docker")
    def test_state_symlink_refuses_before_chmod_or_token_creation(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.root / "data").symlink_to(Path(outside.name), target_is_directory=True)
        with patch.object(Agent,"command") as docker, self.assertRaises(IsolationError):
            IsolatedAgent(self.root,"LearnGraph/UpdateFixture",self.image,["compose.json"])
        docker.assert_not_called()
        self.assertEqual(list(Path(outside.name).iterdir()), [])

    def test_compose_project_mismatch(self):
        self.change(lambda c: c.update(name="learngraph"))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_external_volume_refused(self):
        self.change(lambda c: c["volumes"]["data"].update(external=True, name="learngraph_learngraph-data"))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_readonly_production_mount_and_mount_option_aliases_refused(self):
        for mount in ["learngraph_learngraph-data:/extra:ro", "data:/data:rw,shared", "data:/var/run/docker.sock", "data:/data:ro:extra"]:
            with self.subTest(mount=mount):
                self.change(lambda c: c["services"]["app"].update(volumes=[mount]))
                with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_unrecognized_privileged_directives_refused(self):
        for field in ["cap_add", "configs", "secrets", "develop", "post_start", "use_api_socket", "pid", "ipc", "devices"]:
            with self.subTest(field=field):
                self.change(lambda c: c["services"]["app"].update({field: []}))
                with self.assertRaises(IsolationError): validate_fixture(self.root)
                self.change(lambda c: c["services"]["app"].pop(field))

    def test_foreign_image_override_refused(self):
        (self.root / "docker-compose.update.json").write_text(json.dumps({"services":{"app":{"image":"learngraph:local"}}}))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_compose_interpolation_cannot_import_host_database_url(self):
        self.change(lambda c: c["services"]["app"]["environment"].update(LEARNGRAPH_DATABASE_URL="${LEARNGRAPH_DATABASE_URL}"))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_provisioner_identity_and_foreign_endpoint_environment_cannot_be_injected(self):
        for key,value in [("HOSTNAME","learngraph-app-1"),("DOCKER_HOST","unix:///var/run/docker.sock"),("LEARNGRAPH_DATABASE_DOCKER_NETWORK","learngraph_default"),("LEARNGRAPH_SANDBOXD_URL","http://production:8090")]:
            self.change(lambda c:c["services"]["app"]["environment"].update({key:value}))
            with self.subTest(key=key), self.assertRaises(IsolationError): validate_fixture(self.root)
            self.change(lambda c:c["services"]["app"]["environment"].pop(key))

    def owned_container(self):
        return {"Name": "/" + self.project + "-app-1", "Config": {"Labels": {OWNER:self.marker["owner"], "com.docker.compose.project":self.project, "com.docker.compose.project.working_dir":str(self.root), "com.docker.compose.service":"app"}, "Env":["LEARNGRAPH_DATABASE_URL=sqlite:////data/learngraph.db"]}, "Mounts":[{"Type":"volume","Name":self.project + "_data","Destination":"/data","RW":True}], "NetworkSettings":{"Networks":{self.project + "_default":{}}}}

    def test_runtime_readonly_foreign_volume_refused_before_exec(self):
        agent = self.agent()
        container = self.owned_container()
        container["Mounts"].append({"Type":"volume","Name":"learngraph_learngraph-data","Destination":"/foreign","RW":False})
        with patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
            agent._verify_container(container, self.marker)
        docker.assert_not_called()

    def test_runtime_changed_mount_target_and_readonly_flag_refused(self):
        agent = self.agent()
        for edit in [lambda m:m.update(Name="learngraph_learngraph-data",RW=False), lambda m:m.update(Destination="/other")]:
            container = self.owned_container(); edit(container["Mounts"][0])
            with self.subTest(container=container["Mounts"]), patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
                agent._verify_container(container, self.marker)
            docker.assert_not_called()

    def test_shell_gate_rejects_foreign_compose_before_client(self):
        agent = self.agent()
        with patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
            dispatch(agent,["compose","--project-name","learngraph","-f","docker-compose.yml","up","-d"])
        docker.assert_not_called()

    def test_shell_gate_rejects_readonly_formal_volume_before_docker(self):
        agent = self.agent()
        with patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
            backup_helper(agent,["run","--rm","-v","learngraph_learngraph-data:/data:ro","-v",str(self.root / "backups")+":/backup","alpine","tar","czf","/backup/probe-volume.tar.gz","-C","/data","."])
        docker.assert_not_called()

    def test_shell_metadata_templates_are_closed_and_preserve_mount_modes(self):
        item = {"Config":{"Labels":{"com.docker.compose.service":"app"}},"Image":"sha256:fixture","Mounts":[{"Type":"volume","Name":"owned","RW":True,"Destination":"/data"},{"Type":"volume","Name":"readonly","RW":False,"Destination":"/other"}],"State":{"Health":{"Status":"healthy"}}}
        self.assertEqual(metadata_format(item,'{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}'),'owned readonly ')
        self.assertEqual(metadata_format(item,'{{range .Mounts}}{{if and .RW (eq .Type "volume")}}{{.Name}} {{end}}{{end}}'),'owned ')
        self.assertEqual(metadata_format(item,'{{.Image}} {{.State.Health.Status}}'),'sha256:fixture healthy')
        with self.assertRaises(IsolationError): metadata_format(item,'{{json .Config.Env}}')

    def test_unknown_docker_commands_fail_closed(self):
        agent = self.agent()
        for args in [["volume","prune"],["container","restart","learngraph-app-1"],["context","use","other"],["system","prune"],["exec","--privileged","learngraph-app-1","true"],["build","-t",self.image+":v1","--output","/foreign",str(self.root)]]:
            with self.subTest(action=args[:2]), patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
                agent.command(args)
            docker.assert_not_called()

    def test_private_raw_docker_mutation_cannot_skip_the_gate(self):
        agent = self.agent()
        for args in [["exec","learngraph-app-1","true"],["run","-v","learngraph_learngraph-data:/data:ro","alpine","true"],["compose","--project-name","learngraph","up","-d"],["volume","rm","learngraph_learngraph-data"]]:
            with self.subTest(args=args), patch.object(Agent,"command") as docker, self.assertRaises(IsolationError): agent._raw(args)
            docker.assert_not_called()

    def test_ci_replay_on_local_or_self_hosted_daemon_refused_before_docker(self):
        for environment in [{},{"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"self-hosted"},{"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted","ACT":"true"}]:
            with self.subTest(environment=environment), patch.object(Agent,"command") as client, self.assertRaises(IsolationError):
                require_fresh_daemon(self.root,environment=environment,inspect=client)
            client.assert_not_called()

    def test_ci_rejects_any_existing_container_volume_or_custom_network(self):
        environment={"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted"}
        for blocked in ["containers","volumes","networks"]:
            def inspect(args):
                if args[0]=="context": return "unix:///var/run/docker.sock"
                if args[0]=="ps": return "formal-container" if blocked=="containers" else ""
                if args[0]=="volume": return "learngraph_learngraph-data" if blocked=="volumes" else ""
                return "bridge\nhost\nnone"+("\nlearngraph-egress" if blocked=="networks" else "")
            with self.subTest(blocked=blocked), self.assertRaises(IsolationError): require_fresh_daemon(self.root,environment=environment,inspect=inspect)

    def test_ci_accepts_fresh_local_github_hosted_daemon(self):
        def inspect(args):
            if args[0]=="context": return "unix:///var/run/docker.sock"
            if args[0]=="network": return "bridge\nhost\nnone"
            return ""
        require_fresh_daemon(self.root,environment={"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted"},inspect=inspect)

    def test_compose_fused_flags_and_unknown_service_fail_closed(self):
        agent = self.agent()
        for args in [["up","--project-name=learngraph"],["up","--file=foreign"],["ps","sandboxd"],["up","--scale","app=3"],["run","--rm","--no-deps","app","python","-c","pass","--volume=foreign:/data"]]:
            with self.subTest(args=args), patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError): agent.compose(args)
            docker.assert_not_called()

    def test_foreign_writable_bind_refused(self):
        self.change(lambda c: c["services"]["app"]["volumes"].append({"type":"bind","source":"/production","target":"/other"}))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_external_network_refused(self):
        self.change(lambda c: c.update(networks={"default":{"external":True,"name":"learngraph_default"}}))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_production_port_refused(self):
        self.change(lambda c: c["services"]["app"].update(ports=["127.0.0.1:18000:8000"]))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_external_database_url_refused(self):
        self.change(lambda c: c["services"]["app"]["environment"].update(LEARNGRAPH_DATABASE_URL="postgresql://production/main"))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_foreign_data_root_refused(self):
        self.change(lambda c: c["services"]["app"]["environment"].update(LEARNGRAPH_DATA_ROOT="/production"))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_wrong_http_port_refused_before_any_http_request(self):
        agent = self.agent()
        with patch.object(agent,"verify_isolation",return_value=self.marker), patch.object(agent,"containers",return_value={"app":{"NetworkSettings":{"Ports":{"8000/tcp":[{"HostIp":"127.0.0.1","HostPort":"18000"}]}}}}), self.assertRaises(IsolationError):
            agent.api_url()

    def test_override_cannot_change_volumes(self):
        (self.root / "docker-compose.update.json").write_text(json.dumps({"services":{"app":{"volumes":["learngraph_learngraph-data:/data"]}}}))
        with self.assertRaises(IsolationError): validate_fixture(self.root)

    def test_inherited_project_cannot_redirect_compose(self):
        agent = self.agent()
        with patch.dict(os.environ, {"COMPOSE_PROJECT_NAME":"learngraph"}), patch.object(Agent, "command", return_value="") as docker:
            agent.compose(["ps", "--all", "--quiet", "app"])
        self.assertEqual(docker.call_args.args[0][:3], ["compose", "--project-name", self.project])

    def test_compose_late_project_override_refused(self):
        agent = self.agent()
        with patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
            agent.compose(["--project-name","foreign","up","-d"])
        docker.assert_not_called()

    def test_compose_run_cannot_add_foreign_volume(self):
        agent = self.agent()
        with patch.object(agent,"_raw") as docker, self.assertRaises(IsolationError):
            agent.compose(["run","--volume","foreign:/data","app"])
        docker.assert_not_called()

    def test_exec_to_foreign_container_refused_before_exec(self):
        agent = self.agent()
        foreign = {"Config":{"Labels":{}},"Name":"/foreign","Mounts":[],"NetworkSettings":{"Networks":{}}}
        def raw(args, timeout=60):
            return json.dumps([foreign]) if args[0]=="inspect" else ""
        with patch.object(agent, "_raw", side_effect=raw) as docker, self.assertRaises(IsolationError):
            agent.command(["exec", "foreign", "touch", "/data/sentinel"])
        self.assertFalse(any(call.args[0][0]=="exec" for call in docker.call_args_list))

    def test_existing_project_resource_without_owner_refused(self):
        agent = self.agent()
        def raw(args, timeout=60):
            if args[:2] == ["volume","ls"]: return self.project + "_data"
            if args[:2] == ["volume","inspect"]: return json.dumps([{"Labels":{"com.docker.compose.project":self.project}}])
            return ""
        with patch.object(agent, "_raw", side_effect=raw), self.assertRaises(IsolationError): agent.verify_isolation()

    def test_foreign_standalone_network_refused(self):
        agent = self.agent()
        with patch.object(agent, "_raw", return_value="") as docker, self.assertRaises(IsolationError):
            agent.command(["run","--network","learngraph_default","unused"])
        self.assertFalse(any(call.args[0][0]=="run" for call in docker.call_args_list))

    def test_guard_is_active_with_python_optimization(self):
        (self.root / MARKER).unlink()
        result = subprocess.run([os.sys.executable,"-O","-c", "from pathlib import Path; from updater.tests.docker_isolation import validate_fixture; validate_fixture(Path(" + repr(str(self.root)) + "))"],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("IsolationError",result.stderr)

    def shell_refuses(self, *, expected=True, project=None, directory=None, owner=None, volume=None, reason):
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts/docker-update.sh", scripts / "docker-update.sh")
        shutil.copyfile(self.root / "compose.json", self.root / "docker-compose.yml")
        binary = self.root / "bin"
        binary.mkdir()
        stub = binary / "docker"
        stub.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  'compose version') exit 0 ;;
  *' config --services') printf '%s\\n' app preview egress-proxy database-migration-agent ;;
  *' ps -q app'|*' ps --all -q'|*' ps --all -q app'|*' ps --all -q sandboxd') echo fixture-id ;;
  *'com.docker.compose.project.working_dir'*) echo "$SCOPE_DIRECTORY" ;;
  *'io.learngraph.acceptance-owner'*) echo "$SCOPE_OWNER" ;;
  *'com.docker.compose.service'*) echo "${SCOPE_SERVICE:-app}" ;;
  'volume inspect '*) echo "$SCOPE_VOLUME_PROJECT" ;;
  *'com.docker.compose.project'*) echo "$SCOPE_PROJECT" ;;
  *'eq .Type "bind"'*) : ;;
  *'.Mounts'*) echo fixture-data ;;
  exec*) echo 'UNSAFE_EXEC_REACHED'; exit 98 ;;
  *) exit 97 ;;
esac
''')
        stub.chmod(0o755)
        env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "DOCKER_LOG":str(self.root / "docker.log"), "SCOPE_DIRECTORY":str(directory or self.root), "SCOPE_PROJECT":project or self.project, "SCOPE_OWNER":owner or self.marker["owner"], "SCOPE_VOLUME_PROJECT":volume or self.project}
        env.pop("LEARNGRAPH_EXPECTED_PROJECT", None)
        if expected: env["LEARNGRAPH_EXPECTED_PROJECT"] = self.project
        result = subprocess.run(["bash",str(scripts / "docker-update.sh"),"--backup-only"],capture_output=True,text=True,env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(reason, result.stderr)
        log = (self.root / "docker.log").read_text() if (self.root / "docker.log").exists() else ""
        self.assertNotIn("exec ", log)
        self.assertNotIn("UNSAFE_EXEC_REACHED",result.stdout)
        if not expected: self.assertEqual(log, "")
        self.assertFalse((self.root / "backups").exists())
        return env, scripts / "docker-update.sh"

    def shell_helpers(self, commands, **overrides):
        env, script = self.shell_refuses(expected=False, reason="LEARNGRAPH_EXPECTED_PROJECT")
        source = script.read_text()
        self.assertTrue(source.rstrip().endswith('main "$@"'))
        helpers = script.with_name("helpers.sh")
        helpers.write_text(source.rsplit('\nmain "$@"', 1)[0] + '\nDECLARED_SERVICES="${SCOPE_DECLARED_SERVICES:-app}"\n' + commands + '\n')
        env.update(LEARNGRAPH_EXPECTED_PROJECT=self.project, **overrides)
        result = subprocess.run(["bash",str(helpers)],capture_output=True,text=True,env=env)
        log = (self.root / "docker.log").read_text() if (self.root / "docker.log").exists() else ""
        self.assertNotIn("exec ", log)
        self.assertNotIn("run ", log)
        return result, log

    @unittest.skipIf(os.name == "nt", "Optional-service checks run in isolated Linux Docker")
    def test_shell_undeclared_optional_service_never_queried(self):
        result, log = self.shell_helpers("service_container sandboxd")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未声明服务", result.stderr)
        self.assertEqual(log, "")

    @unittest.skipIf(os.name == "nt", "Shell path tests run in isolated Linux Docker")
    def test_shell_prune_preserves_space_and_newline_filename_boundaries(self):
        result, _ = self.shell_helpers('''BACKUP_DIR="$REPO_ROOT/backups with spaces"$'\n'"and newline"
mkdir -p "$BACKUP_DIR"
printf old > "$BACKUP_DIR/primary-old.backup.zip"
touch -t 202001010000 "$BACKUP_DIR/primary-old.backup.zip"
printf new > "$BACKUP_DIR/primary-new.backup.zip"
printf untouched > "$REPO_ROOT/sentinel"
prune_backups 1
[ ! -f "$BACKUP_DIR/primary-old.backup.zip" ] && [ -f "$BACKUP_DIR/primary-new.backup.zip" ] && [ -f "$REPO_ROOT/sentinel" ]''')
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipIf(os.name == "nt", "Shell health tests run in isolated Linux Docker")
    def test_shell_requires_health_of_all_three_business_roles(self):
        result, _ = self.shell_helpers('''service_container() { printf '%s' "$1"; }
docker() { if [ "$4" = preview ]; then printf 'sha256:fixture unhealthy'; else printf 'sha256:fixture healthy'; fi; }
if business_healthy sha256:fixture; then exit 99; fi
docker() { printf 'sha256:fixture healthy'; }
business_healthy sha256:fixture''')
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipIf(os.name == "nt", "Shell compatibility tests run in isolated Linux Docker")
    def test_shell_incompatible_schema_is_rejected_before_replace(self):
        result, _ = self.shell_helpers('''docker() { if [ "$5" = old ]; then printf 1; else printf 2; fi; }
verify_image_compatibility old target''')
        self.assertNotEqual(result.returncode,0)
        self.assertIn("兼容标记不同",result.stderr)

    @unittest.skipIf(os.name == "nt", "Shell compatibility tests run in isolated Linux Docker")
    def test_shell_equal_schema_contract_is_accepted(self):
        result, _ = self.shell_helpers('''docker() { printf 1; }
verify_image_compatibility old target''')
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipIf(os.name == "nt", "Shell path tests run in isolated Linux Docker")
    def test_shell_normalize_resolves_nonexistent_child_below_symlink(self):
        result, _ = self.shell_helpers('''mkdir "$REPO_ROOT/outside"
ln -s "$REPO_ROOT/outside" "$REPO_ROOT/alias"
[ "$(normalize_path "$REPO_ROOT/alias/not-created")" = "$REPO_ROOT/outside/not-created" ]''')
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipIf(os.name == "nt", "Optional-service checks run in isolated Linux Docker")
    def test_shell_declared_owned_optional_service_and_volume_accepted(self):
        result, _ = self.shell_helpers("service_container sandboxd; verify_backup_volume fixture-volume", SCOPE_DECLARED_SERVICES="app\nsandboxd", SCOPE_SERVICE="sandboxd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "fixture-id")

    @unittest.skipIf(os.name == "nt", "Optional-service checks run in isolated Linux Docker")
    def test_shell_declared_optional_service_cannot_resolve_foreign_project(self):
        result, _ = self.shell_helpers("service_container sandboxd", SCOPE_DECLARED_SERVICES="app\nsandboxd", SCOPE_SERVICE="sandboxd", SCOPE_PROJECT="foreign")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("项目不符", result.stderr)

    @unittest.skipIf(os.name == "nt", "Optional-service checks run in isolated Linux Docker")
    def test_shell_declared_optional_service_name_must_match(self):
        result, _ = self.shell_helpers("service_container sandboxd", SCOPE_DECLARED_SERVICES="app\nsandboxd", SCOPE_SERVICE="app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("服务名称不符", result.stderr)

    @unittest.skipIf(os.name == "nt", "Shell guard checks run in the isolated Linux container")
    def test_shell_missing_project_calls_no_docker(self): self.shell_refuses(expected=False, reason="LEARNGRAPH_EXPECTED_PROJECT")

    @unittest.skipIf(os.name == "nt", "Shell guard checks run in the isolated Linux container")
    def test_shell_wrong_working_directory_calls_no_exec(self): self.shell_refuses(directory=self.root / "elsewhere", reason="工作目录")

    @unittest.skipIf(os.name == "nt", "Linux scope comparison is case sensitive")
    def test_shell_linux_directory_case_mismatch_calls_no_exec(self): self.shell_refuses(directory=Path(str(self.root).upper()), reason="工作目录")

    @unittest.skipIf(os.name == "nt", "Shell guard checks run in the isolated Linux container")
    def test_shell_wrong_project_calls_no_exec(self): self.shell_refuses(project="foreign", reason="项目不符")

    @unittest.skipIf(os.name == "nt", "Shell guard checks run in the isolated Linux container")
    def test_shell_wrong_owner_calls_no_exec(self): self.shell_refuses(owner="f" * 32, reason="所有权标签")

    @unittest.skipIf(os.name == "nt", "Shell guard checks run in the isolated Linux container")
    def test_shell_foreign_volume_calls_no_exec(self): self.shell_refuses(volume="foreign", reason="卷不属于")


if __name__ == "__main__": unittest.main()

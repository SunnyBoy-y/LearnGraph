from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from updater.agent import Agent, COMPAT_LABEL, SERVICES, UpdateError, handler, process_lock, version


class FakeAgent(Agent):
    def __init__(self, root):
        super().__init__(root, "SunnyBoy-y/LearnGraph", "ghcr.io/sunnyboy-y/learngraph")
        self.running = "old"
        self.operations = []
        self.break_target = False
        self.break_rollback = False
        self.target_compatibility = "1"
        self.break_backup = False
        self.state["releases"] = [{"version": "v1.1.0"}]

    def containers(self):
        return {name: {"Id": name, "Image": self.running, "Config": {"Labels": {
            "org.opencontainers.image.version": "v1.0.0" if self.running == "old" else "v1.1.0",
        }}} for name in SERVICES}

    def current(self):
        return {"version": "v1.0.0" if self.running == "old" else "v1.1.0", "image": self.running, "compatibility": "1"}

    def verify_configuration(self, containers):
        pass

    def image_info(self, image):
        return {"Id": "new" if ":v1.1.0" in image else image, "Config": {"Labels": {
            COMPAT_LABEL: self.target_compatibility if image != "old" else "1",
            "org.opencontainers.image.version": "v1.1.0",
            "org.opencontainers.image.source": "https://github.com/SunnyBoy-y/LearnGraph",
        }}}

    def command(self, args, timeout=60):
        self.operations.append(args)
        return ""

    def backup(self, job, container):
        self.operations.append(["backup"])
        if self.break_backup:
            raise UpdateError("Backup failed")

    def replace(self, images):
        # Simulate a failure AFTER a partial container replacement.
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        assert persisted["jobs"][-1]["status"] in {"applying", "rolling_back"}
        self.operations.append(["replace", images["app"]])
        self.write_images(images)
        self.running = images["app"]
        if self.running == "new" and self.break_target:
            raise UpdateError("New image unhealthy")
        if self.running == "old" and self.break_rollback:
            raise UpdateError("Old image unhealthy")


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = FakeAgent(Path(self.tmp.name))

    def submit_sync(self, **kwargs):
        # Keep real acceptance persistence/idempotency, execute deterministically.
        with patch("updater.agent.threading.Thread"):
            job = self.agent.submit(request_id="a" * 16, **kwargs)
        self.agent.execute(job["id"])
        return self.agent.state["jobs"][-1]

    def test_version_comparison_is_numeric_and_stable_only(self):
        self.assertEqual(version("v0.5"), version("0.5.0"))
        self.assertGreater(version("v0.5.1"), version("v0.5"))
        self.assertGreater(version("v1.10.0"), version("v1.9.9"))
        for value in ["v1.0.0-rc.1", "v01.0.0", "latest", "v1;sh", "../1.0.0"]:
            with self.assertRaises(ValueError):
                version(value)

    def test_success_keeps_pins_backup_and_managed_compose_selection(self):
        job = self.submit_sync(target="v1.1.0")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["previous_images"], dict.fromkeys(SERVICES, "old"))
        operations = self.agent.operations
        self.assertLess(next(i for i, op in enumerate(operations) if op[0] == "pull"), operations.index(["backup"]))
        self.assertLess(operations.index(["backup"]), operations.index(["replace", "new"]))
        selection = json.loads(self.agent.override.read_text())
        self.assertEqual(selection["services"]["preview"]["image"], "new")

    def test_health_failure_recovers_all_services(self):
        self.agent.break_target = True
        job = self.submit_sync(target="v1.1.0")
        self.assertEqual(job["status"], "rolled_back")
        self.assertEqual(self.agent.running, "old")
        self.assertIn("v1.1.0", self.agent.state["failed_versions"])

    def test_failed_rollback_is_never_reported_as_success(self):
        self.agent.break_target = self.agent.break_rollback = True
        job = self.submit_sync(target="v1.1.0")
        self.assertEqual(job["status"], "rollback_failed")

    def test_incompatible_database_never_replaces_containers(self):
        self.agent.target_compatibility = "2"
        job = self.submit_sync(target="v1.1.0")
        self.assertEqual(job["status"], "failed")
        self.assertFalse(any(op[0] == "replace" for op in self.agent.operations))

    def test_failed_backup_never_replaces_containers(self):
        self.agent.break_backup = True
        job = self.submit_sync(target="v1.1.0")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.agent.running, "old")
        self.assertFalse(self.agent.override.exists())

    def test_unpublished_or_injected_targets_are_rejected(self):
        for target in ["v9.0.0", "ghcr.io/evil/app:latest", "--help", "../v1"]:
            with self.assertRaises(UpdateError):
                self.agent.submit(target=target)

    def test_idempotency_returns_same_job_and_concurrent_job_is_rejected(self):
        with patch("updater.agent.threading.Thread") as worker:
            first = self.agent.submit(target="v1.1.0", request_id="test-request-12345")
            same = self.agent.submit(target="v1.1.0", request_id="test-request-12345")
            self.assertEqual(first["id"], same["id"])
            self.assertEqual(worker.call_count, 1)
            with self.assertRaises(UpdateError):
                self.agent.submit(target="v1.1.0", request_id="another-request-123")

    def test_manual_rollback_keeps_new_writes_and_disables_auto_install(self):
        original = self.submit_sync(target="v1.1.0")
        self.agent.state["auto_install"] = True
        self.agent.break_backup = False
        self.agent.operations.clear()
        with patch("updater.agent.threading.Thread"):
            job = self.agent.submit(rollback_id=original["id"], request_id="rollback-request-12")
        self.agent.execute(job["id"])
        result = self.agent.state["jobs"][-1]
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.agent.running, "old")
        self.assertFalse(self.agent.state["auto_install"])
        self.assertEqual([op[0] for op in self.agent.operations].count("backup"), 1)
        self.assertFalse(any(op[0] == "pull" for op in self.agent.operations))

    def test_restart_recovers_journal_written_before_container_mutation(self):
        original = self.submit_sync(target="v1.1.0")
        original["status"] = "applying"
        self.agent.save()
        restarted = FakeAgent(self.agent.root)
        restarted.running = "new"
        restarted.recover()
        self.assertEqual(restarted.running, "old")
        self.assertEqual(restarted.state["jobs"][-1]["status"], "rolled_back")
        self.assertIn("v1.1.0", restarted.state["failed_versions"])

    def test_restart_before_mutation_does_not_recreate_containers(self):
        with patch("updater.agent.threading.Thread"):
            self.agent.submit(target="v1.1.0")
        self.agent.recover()
        self.assertEqual(self.agent.state["jobs"][-1]["status"], "failed")
        self.assertFalse(any(op[0] == "replace" for op in self.agent.operations))

    def test_release_filter_etag_and_304_preserve_cached_versions(self):
        payload = [{"tag_name": "v1.9.0", "body": "notes"}, {"tag_name": "v1.10.0"},
                   {"tag_name": "v9.0.0", "prerelease": True}, {"tag_name": "v8.0.0", "draft": True},
                   {"tag_name": "../../bad"}, {"tag_name": "v2.0.0-rc.1"}, {"tag_name": "v0.5"}]
        response = io.BytesIO(json.dumps(payload).encode())
        response.headers = {"ETag": '"abc"'}
        with patch("updater.agent.urlopen", return_value=response):
            self.agent.check()
        self.assertEqual([r["version"] for r in self.agent.state["releases"]], ["v1.10.0", "v1.9.0", "v0.5"])
        with patch("updater.agent.urlopen", side_effect=HTTPError("url", 304, "cached", {}, None)) as fetch:
            self.agent.check()
        self.assertEqual(fetch.call_args.args[0].get_header("If-none-match"), '"abc"')
        self.assertEqual(self.agent.state["releases"][0]["version"], "v1.10.0")
        self.assertIsNone(self.agent.state["check_error"])

    def test_rate_limit_preserves_cache_and_prevents_automatic_install(self):
        with patch("updater.agent.urlopen", side_effect=HTTPError("url", 403, "limit", {}, None)):
            self.agent.check()
        self.assertEqual(self.agent.state["releases"][0]["version"], "v1.1.0")
        self.assertIn("403", self.agent.state["check_error"])

    def test_configuration_drift_is_rejected(self):
        containers = self.agent.containers()
        for container in containers.values():
            container["Config"]["Labels"].update({"com.docker.compose.project.working_dir": str(self.agent.root),
                                                   "com.docker.compose.config-hash": "original"})
            container["State"] = {"Health": {"Status": "healthy"}}
        with patch.object(self.agent, "compose", return_value="app changed\npreview original\negress-proxy original"):
            with self.assertRaisesRegex(UpdateError, "Compose"):
                Agent.verify_configuration(self.agent, containers)

    def test_missing_local_running_image_is_reported_before_upgrade(self):
        with patch.object(self.agent, 'compose', return_value='container-id'), \
             patch.object(self.agent, 'command', return_value=json.dumps([{'Image':'missing', 'Config':{'Labels':{}}}])), \
             patch.object(self.agent, 'image_info', side_effect=UpdateError('No such image')):
            with self.assertRaisesRegex(UpdateError, '无法保证回滚'):
                Agent.current(self.agent)

    def test_process_lock_prevents_two_agents_and_releases_on_exit(self):
        path = self.agent.state_dir / 'lock'
        with process_lock(path):
            with self.assertRaises((SystemExit, OSError)):
                with process_lock(path):
                    self.fail('Second updater obtained the lock')
        with process_lock(path):
            pass

    def test_scheduler_installs_only_when_enabled_and_not_previously_failed(self):
        for enabled, failed, expected in [(False, False, 0), (True, False, 1), (True, True, 0)]:
            self.agent.state['auto_install'] = enabled
            self.agent.state['failed_versions'] = ['v1.1.0'] if failed else []
            stop = Mock()
            stop.is_set.side_effect = [False, True]
            with patch.object(self.agent, 'check'), patch.object(self.agent, 'submit') as submit:
                self.agent.poll(stop, 3600)
                self.assertEqual(submit.call_count, expected)

    def test_http_auth_origin_and_input_boundaries(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.agent))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        for headers, status in [({}, 401), ({"Authorization": "Bearer " + self.agent.token, "Origin": "https://evil.test"}, 403)]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + "/v1/status", headers=headers), timeout=5)
            self.assertEqual(error.exception.code, status)
        headers = {"Authorization": "Bearer " + self.agent.token, "Content-Type": "application/json"}
        invalid = Request(url + "/v1/jobs", data=b'{"version":"v1.1.0"}', headers=headers)
        with self.assertRaises(HTTPError) as error:
            urlopen(invalid, timeout=5)
        self.assertEqual(error.exception.code, 422)
        with urlopen(Request(url + "/v1/policy", data=b'{"auto_install":true}', headers=headers), timeout=5) as response:
            self.assertTrue(json.load(response)["auto_install"])


if __name__ == "__main__":
    unittest.main()

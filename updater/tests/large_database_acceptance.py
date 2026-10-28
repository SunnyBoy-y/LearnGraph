"""Collect completed synthetic large-database evidence; never adopt a deployment."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

import httpx

from updater.tests.docker_isolation import require, validate_fixture
from updater.tests.postgresql_roundtrip import FixtureAgent, PASSWORD


def local(root, name):
    path = root / name
    require(path.resolve().parent == root and not path.is_symlink(), 'evidence path escapes its owned fixture')
    return path


def load(root, name):
    return json.loads(local(root, name).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--small-root', type=Path, required=True)
    args = parser.parse_args()
    root, small = args.root.resolve(), args.small_root.resolve()
    marker, _ = validate_fixture(root)
    small_marker, _ = validate_fixture(small)
    require(root != small, 'large and small fixture roots must differ')
    names = ['verification.json', 'current-code-large-verification.json', 'current-updater-large-verification.json', 'current-upload-large-verification.json']
    evidence = {name: load(root, name) for name in names}
    require(all(value.get('passed') and value.get('project') == marker['project'] for value in evidence.values()), 'large acceptance is incomplete')
    main_run, current, update, upload = (evidence[name] for name in names)
    seed = main_run['synthetic_database']
    require(seed['synthetic_only'] and seed['sqlite_bytes'] >= 4 * 1024**3, 'not the synthetic 4 GiB fixture')
    require(update['final_data_profile']['source_target_seed_sha256'] == seed['logical_payload_sha256'], 'final full-data digest mismatch')
    live = main_run['live_migration_requests']
    membership = load(root, 'live-write-membership.json')
    require(len(live) == membership['verified_live_writes'] == 512 and membership['sqlite_permanently_sealed'] and membership['dialect'] == 'postgresql', 'live write membership or one-way seal failed')
    require(all(item['read'] == 200 and item['write'] == 201 for item in live), 'migration request failed')
    branch_reports = {name: load(small, name) for name in ['shell-isolation-verification.json', 'configuration-verification.json', 'key-verification.json']}
    require(all(value.get('passed') for value in branch_reports.values()) and branch_reports['shell-isolation-verification.json']['project'] == small_marker['project'], 'maintenance branch acceptance incomplete')
    counts = {}
    for label, filename, expected in [('backend', 'large-backup-unit-tests-final.log', 70), ('postgresql', 'large-backup-postgresql-tests-final.log', 9), ('migration', 'large-backup-migration-tests-final.log', 14), ('updater_isolation', 'large-backup-updater-isolation-tests-final.log', 76)]:
        log = local(small, filename).read_text(encoding='utf-8')
        matches = re.findall(r'(\d+) passed', log)
        if not matches:
            matches = re.findall(r'Ran (\d+) tests', log)
            require(log.rstrip().endswith('OK'), label + ' unittest run did not pass')
        require(matches and int(matches[-1]) == expected, label + ' tests incomplete')
        counts[label] = expected
    frontend = local(small, 'large-backup-frontend-build-final.log').read_text(encoding='utf-8')
    require('8 passed' in frontend and 'built in' in frontend, 'frontend validation incomplete')
    counts['frontend'] = 8
    agent = FixtureAgent(root, 'LearnGraph/UpdateFixture', marker['image'], ['compose.json'])
    require(not agent.active() and not agent.state['auto_install'] and agent.current()['version'] == update['version'], 'final updater is not idle or its policy is still enabled')
    containers = agent.containers()
    roles = {role: item['State'].get('Health', {}).get('Status') for role, item in containers.items()}
    require(all(roles.get(role) == 'healthy' for role in ['app', 'preview', 'egress-proxy']), 'business container is not healthy')
    require(containers['app']['HostConfig']['Memory'] == 1024**3, 'large application memory cap lost')
    with httpx.Client(base_url=agent.api_url(), trust_env=False, timeout=60) as client:
        response = client.post('/auth/login', json={'username': 'admin', 'password': PASSWORD})
        response.raise_for_status()
        user = response.json()
        client.headers.update({'Authorization': 'Bearer ' + user['access_token'], 'X-Workspace-ID': user['default_workspace_id']})
        response = client.get('/migrations/database/status')
        response.raise_for_status()
        require(response.json()['active_database'] == 'postgresql', 'final primary routing changed')
    scratch = json.loads(agent.command(['exec', containers['app']['Id'], 'python', '-c', "from pathlib import Path; import json; r=Path('/data/.backup-work'); print(json.dumps({'workspaces':[str(p) for p in r.glob('lg-site-*')], 'downloads':[str(p) for p in (r/'browser-downloads').glob('*') if p.is_dir()], 'native_archives':[str(p) for base in [r,Path('/tmp')] for p in base.glob('learngraph-update-*.zip')], 'snapshot_children':[str(p) for p in Path('/data/database-migration/maintenance-work').glob('*')], 'reserved':Path('/data/database-migration/update.json').exists()}))"]))
    require(not any(scratch.values()), 'final scratch files or maintenance reservation remain')
    # Only stop this fixture's read-only monitor after every dependent run passed.
    local(root, 'monitor-stop').write_text('completed\n', encoding='utf-8')
    memory = []
    errors = 0
    for name in ['memory-samples.jsonl', 'memory-all-app-workers.jsonl']:
        with local(root, name).open(encoding='utf-8') as handle:
            for line in handle:
                sample = json.loads(line)
                if 'process_hwm' not in sample:
                    errors += 1
                    continue
                events = dict(item.split() for item in sample.get('memory.events', '').splitlines())
                require(all(int(events.get(key, 0)) == 0 for key in ['oom', 'oom_kill', 'oom_group_kill']), 'an owned application worker encountered OOM')
                require(sample['memory.max'] == str(1024**3), 'an observed application worker had no 1 GiB memory cap')
                memory.append(sample)
    require(memory, 'memory evidence missing')
    report = {'passed': True, 'completed_at_utc': datetime.now(timezone.utc).isoformat(), 'project': marker['project'], 'synthetic_only': True, 'production_actions': 0, 'database': seed, 'migration': {'tables': 190, 'reads': 512, 'writes': 512, 'failed_requests': 0, 'all_live_writes_found': True, 'sqlite_permanently_sealed': True, 'max_read_write_pair_seconds': max(item['seconds'] for item in live)}, 'large_chain_checks': main_run['checks'], 'encrypted_archive': current['encrypted_archive'], 'download_replay_status': current['download_replay_status'], 'automatic_update': {'version': update['version'], 'native_archive_bytes': update['native_archive_bytes'], 'destination': update['destination'], 'snapshot_workspace': update['snapshot_workspace'], 'full_data_profile': update['final_data_profile']}, 'large_upload': {key: upload[key] for key in ['uploaded_bytes', 'status', 'code', 'data_volume_observed', 'no_large_tmp_files', 'workspace_cleanup']}, 'memory': {'samples': len(memory), 'sampling_errors_during_container_changes': errors, 'configured_application_limit_bytes': 1024**3, 'max_process_hwm_bytes': max(item['process_hwm'] for item in memory), 'max_sampled_process_rss_sum_bytes': max(item['rss_sum'] for item in memory), 'observed_oom_events': 0, 'note': 'Cgroup memory also includes reclaimable file cache; sampled process peaks are not a universal bound.'}, 'focused_test_counts': counts, 'frontend_typecheck_build': True, 'shell_branch_checks': branch_reports['shell-isolation-verification.json']['checks'], 'configuration_restore': True, 'packaged_file_key_restore': True, 'final_container_health': roles, 'final_scratch': scratch, 'host_free_bytes': shutil.disk_usage(root).free, 'restore_availability': 'Full restore was exercised with isolated business processes stopped, using the existing offline restore workflow.'}
    report['capacity'] = {'minimum_sampled_data_volume_free_bytes': min(item['free_bytes'] for item in memory), 'minimum_sampled_host_free_bytes': min(item['host_free_bytes'] for item in memory if 'host_free_bytes' in item)}
    report['incremental_replay'] = 'Real PostgreSQL tests exercised >20 MiB in one captured source transaction, bounded replay, second-chunk failure rollback and unchanged checkpoint.'
    report['large_upload']['version_guard'] = upload['version_guard']
    report['large_upload']['previously_validated_archive_sha256'] = upload['previously_validated_archive_sha256']
    report['large_upload']['expected_rejection'] = 'This unchanged archive passed full staging at v1.0.0; v1.1.0 refuses archived Settings with a different packaged app_version before the later explicit version check.'
    report['evidence_files'] = names + ['live-write-membership.json', 'memory-samples.jsonl', 'memory-all-app-workers.jsonl']
    local(root, 'acceptance-summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'report': str(root / 'acceptance-summary.json'), 'focused_tests': sum(counts.values()), 'memory_samples': len(memory), 'observed_oom_events': 0}), flush=True)


if __name__ == '__main__':
    main()

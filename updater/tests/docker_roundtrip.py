"""Opt-in REAL Docker acceptance. Uses an isolated project and local fixtures.

Run: python -m updater.tests.docker_roundtrip
No production containers, ports, volumes or remote releases are changed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from updater.agent import Agent, SERVICES, TERMINAL, UpdateError


SERVER = '''import json, os, sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
db = sqlite3.connect('/data/learngraph.db')
db.execute('CREATE TABLE IF NOT EXISTS messages (value TEXT)'); db.commit(); db.close()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        db = sqlite3.connect('/data/learngraph.db')
        if self.path == '/write':
            db.execute("INSERT INTO messages VALUES ('written after upgrade')"); db.commit()
        count = db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]; db.close()
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({'status':'ok','count':count}).encode())
    def log_message(self, *args): pass
HTTPServer(('0.0.0.0',8000), Handler).serve_forever()
'''


class LocalAgent(Agent):
    def command(self, args, timeout=60):
        if args[0] == 'pull':
            # Image publication is simulated with local fixture tags; all
            # Compose/container/backup/health/rollback operations are real.
            self.image_info(args[1])
            return ''
        return super().command(args, timeout)


def wait_job(agent, job):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        data = next(item for item in agent.state['jobs'] if item['id'] == job['id'])
        if data['status'] in TERMINAL:
            return data
        time.sleep(.25)
    raise AssertionError('Fixture update timed out')


def main():
    root = Path(tempfile.mkdtemp(prefix='lg-updater-docker-')).resolve()
    image = 'learngraph-updater-fixture-' + uuid.uuid4().hex[:10]
    project = 'lg-update-test-' + uuid.uuid4().hex[:10]
    agent = LocalAgent(root, 'LearnGraph/UpdateFixture', image, ['compose.json'], timeout=20)
    (root / 'server.py').write_text(SERVER, encoding='utf-8')
    # Reuse a local application base so acceptance does not require an external
    # registry or rebuild the product's large dependencies.
    base = os.environ.get('LEARNGRAPH_UPDATE_TEST_BASE', 'learngraph:local')
    (root / 'Dockerfile').write_text(f'''FROM {base}
ARG VERSION
ARG BROKEN=0
USER root
LABEL org.opencontainers.image.version=$VERSION
LABEL org.opencontainers.image.source=https://github.com/LearnGraph/UpdateFixture
LABEL io.learngraph.update-compatibility=1
ENV BROKEN=$BROKEN
COPY server.py /fixture-server.py
ENTRYPOINT ["python", "/fixture-server.py"]
HEALTHCHECK --interval=1s --timeout=1s --start-period=1s --retries=2 CMD python -c "import os, urllib.request; assert os.environ['BROKEN']=='0'; urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=1)"
''', encoding='utf-8')
    config = {'name': project, 'services': {service: {'image': image + ':v1.0.0', 'volumes': ['fixture-data:/data'],
              'environment': {'LEARNGRAPH_DATABASE_URL': 'sqlite:////data/learngraph.db'}, 'restart': 'no'} for service in SERVICES},
              'volumes': {'fixture-data': {}}}
    (root / 'compose.json').write_text(json.dumps(config), encoding='utf-8')
    results = []
    try:
        for tag, broken in [('v1.0.0', '0'), ('v1.1.0', '0'), ('v1.2.0', '1')]:
            agent.command(['build', '-t', image + ':' + tag, '--build-arg', 'VERSION=' + tag,
                           '--build-arg', 'BROKEN=' + broken, str(root)], 120)
        agent.compose(['up', '-d', '--wait', '--wait-timeout', '30'], 60)
        agent.state['releases'] = [{'version': 'v1.1.0'}, {'version': 'v1.2.0'}]
        job = wait_job(agent, agent.submit(target='v1.1.0', request_id=uuid.uuid4().hex))
        assert job['status'] == 'succeeded', job
        results.append('upgrade: all three containers use v1.1.0 and pass readiness')
        app_id = agent.containers()['app']['Id']
        agent.command(['exec', app_id, 'python', '-c', "import urllib.request; urllib.request.urlopen('http://localhost:8000/write').read()"])
        rollback = wait_job(agent, agent.submit(rollback_id=job['id'], request_id=uuid.uuid4().hex))
        assert rollback['status'] == 'succeeded', rollback
        app_id = agent.containers()['app']['Id']
        count = agent.command(['exec', app_id, 'python', '-c', "import sqlite3; print(sqlite3.connect('/data/learngraph.db').execute('SELECT COUNT(*) FROM messages').fetchone()[0])"])
        assert count == '1', count
        results.append('manual rollback: v1.0.0 restored; post-upgrade write preserved')
        bad = wait_job(agent, agent.submit(target='v1.2.0', request_id=uuid.uuid4().hex))
        assert bad['status'] == 'rolled_back', bad
        assert agent.current()['version'] == 'v1.0.0'
        results.append('unhealthy image: automatic rollback restores original healthy containers')
        # Emulate agent death after a new image was installed but before journal
        # commit. Recovery runs against real containers, using saved image IDs.
        good = wait_job(agent, agent.submit(target='v1.1.0', request_id=uuid.uuid4().hex))
        assert good['status'] == 'succeeded', good
        good['status'] = 'applying'
        agent.save()
        restarted = LocalAgent(root, 'LearnGraph/UpdateFixture', image, ['compose.json'], timeout=20)
        restarted.recover()
        assert restarted.current()['version'] == 'v1.0.0'
        assert restarted.state['jobs'][-1]['status'] == 'rolled_back'
        results.append('agent restart: persisted incomplete transaction restores v1.0.0')
        assert len(list(agent.private.glob('*.db'))) == 4
        results.append('four SQLite backups copied to host and quick_check passed')
        print(json.dumps({'passed': results, 'project': project, 'local_fixture_images': True}, ensure_ascii=False, indent=2))
    finally:
        try:
            agent.compose(['down', '--volumes', '--remove-orphans'], 90)
            tags = agent.command(['image', 'ls', '--format', '{{.Repository}}:{{.Tag}}']).splitlines()
            job_ids = {item['id'] for item in agent.state['jobs']}
            owned = [tag for tag in tags if tag.startswith(image + ':') or any(
                tag.startswith('learngraph-retained:' + job_id + '-') for job_id in job_ids)]
            if owned:
                agent.command(['image', 'rm', *owned], 90)
        finally:
            assert root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith('lg-updater-docker-')
            shutil.rmtree(root)


if __name__ == '__main__':
    main()

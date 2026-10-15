"""Run memory regressions without network or production data mounts."""
from pathlib import Path
import subprocess
import sys

backend = Path(__file__).resolve().parents[1]
command = [
    'docker', 'run', '--rm', '--network', 'none', '--user', '0', '--workdir', '/src',
    '--mount', f'type=bind,source={backend},target=/src,readonly',
    '-e', 'PYTHONPATH=/src', '-e', 'PYTHONDONTWRITEBYTECODE=1',
    '-e', 'LEARNGRAPH_DATABASE_URL=sqlite:////tmp/memory-regression.db',
    '-e', 'LEARNGRAPH_STORAGE_ROOT=/tmp/memory-regression-store',
    '-e', 'LEARNGRAPH_ENV=test', '-e', 'LEARNGRAPH_ENABLE_DEMO_SEED=false',
    '--entrypoint', 'python', 'learngraph:local', '-B',
]
if '--imports' in sys.argv:
    command += ['-c', 'from app.services.memory_job_guard import MemoryJobLease; from app.domain.models import MemoryJobState; from app.services.memory_enhancement import extract_session_memories, summarize_session_context; print("imports_ok")']
else:
    command += ['/src/scripts/verify_memory_regressions.py', *sys.argv[1:]]
result = subprocess.run(command, timeout=240)
raise SystemExit(result.returncode)

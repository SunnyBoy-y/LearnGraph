"""Global test isolation — prevent tests from deleting the real database.

``app.core.database`` creates its SQLAlchemy engine at module import time.  Its
default URL, ``sqlite:///./data/learngraph.db``, resolves to the *real* database
when pytest runs from ``backend/``.  Because the engine is created only once per
process, test modules that set ``LEARNGRAPH_DATABASE_URL`` at their own module
top only win when they are the first importer.

Previously, a module which did not set the URL (for example one under
``security/`` or ``services/``) could import the app first.  The engine then
bound to the real DB, and a later test fixture's ``Base.metadata.drop_all``
would delete every real table, including users and workspaces.

This conftest loads before pytest collects any test module.  It forces a fresh,
process-local scratch DB before app code can be imported. The global app engine
must use this exact scratch database (or memory). Dedicated integration engines
are checked separately before connecting. Docker pytest also requires an owned
test-worker context before creating any temporary directory.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

if Path("/.dockerenv").is_file() and not (
    re.fullmatch(r"lg-(?:pg-maintenance|update-test|sandboxd-test)-[0-9a-f]{10}",os.environ.get("LEARNGRAPH_TEST_DOCKER_PROJECT","")) and
    re.fullmatch(r"[0-9a-f]{32}",os.environ.get("LEARNGRAPH_TEST_DOCKER_OWNER",""))):
    raise pytest.UsageError("Docker test isolation: pytest requires an explicitly owned disposable worker; production container execution refused before creating files")
_TEMP_BASE=Path(tempfile.gettempdir()).resolve()
_REPO=Path(__file__).resolve().parents[2]
if any(_TEMP_BASE.is_relative_to(path) for path in [Path("/data").resolve(),Path("/var/lib/docker").resolve(),_REPO/"data",_REPO/"backend/data"]):
    raise pytest.UsageError("Docker test isolation: production data directories cannot contain pytest scratch data")
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="lg-pytest-"))

# Force isolation rather than setdefault: a developer's shell environment must
# never silently redirect pytest at the production/local-user database.
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_MODELS_DEV_REFRESH_ENABLED"] = "false"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"
# Sandbox bootstrap persists sandbox-runtime.json / bootstrap-source / policy
# files next to the storage root.  The real backend/data must never be touched
# by tests (a bootstrap source cleanup fixture would otherwise wipe the
# operator's persisted image-source choice).
os.environ["LEARNGRAPH_STORAGE_ROOT"] = str(_TEST_ROOT / "storage")
os.environ["LEARNGRAPH_MEMORY_ROOT"] = str(_TEST_ROOT / "memory")
os.environ["LEARNGRAPH_SANDBOX_WORKSPACE_ROOT"] = str(_TEST_ROOT / "sandbox-workspaces")
# Blank wins over a local .env and lets state_root follow each test's storage
# root; a developer's persisted activation/restore journal must never be used.
os.environ["LEARNGRAPH_DATABASE_MIGRATION_STATE_DIR"] = ""
# Local .env files commonly configure LEARNGRAPH_SANDBOX_PREBUILT_IMAGE (e.g.
# an ACR release reference).  Bootstrap mode tests assume "no prebuilt image
# configured" and must never inherit a developer's registry reference; an
# empty env value wins over the .env file in pydantic-settings.
os.environ["LEARNGRAPH_SANDBOX_PREBUILT_IMAGE"] = ""
# Pipecat sentence splitting must use the checked-in corpus in a clean checkout.
# Do not depend on a developer's NLTK cache or download data during CI.
os.environ["NLTK_DATA"] = str(Path(__file__).resolve().parents[2] / "docker" / "nltk_data")


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest's tmp_path fixtures inside the writable project tree."""

    if not config.option.basetemp:
        temp_root = Path(__file__).parent / ".pytest-tmp"
        # pytest creates basetemp itself, but does not create missing parents.
        # This directory is ignored by Git and absent on a clean CI runner.
        temp_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = temp_root / f"run-{os.getpid()}"


def _assert_engine_is_not_real_database() -> None:
    """Allow only this process's scratch engine before any possible drop_all.

    A denylist for learngraph.db misses PostgreSQL and custom SQLite filenames,
    particularly when an early plugin imported the app before this conftest.
    Dedicated PostgreSQL integration engines use their separate ownership gate.
    """

    import sys

    database_module = sys.modules.get("app.core.database")
    if database_module is None:
        return

    from sqlalchemy.engine import make_url
    engine_url = make_url(database_module.engine.url)
    safe = engine_url.get_backend_name() == "sqlite" and (
        engine_url.database == ":memory:" or engine_url.database is not None and
        Path(engine_url.database).resolve() == (_TEST_ROOT / "test.db").resolve())
    if not safe:
        raise pytest.UsageError(
            "pytest application engine must use the process-local scratch SQLite. "
            "Refusing PostgreSQL, custom/production SQLite and early-plugin bindings "
            "before any test can execute drop_all; no database URL is disclosed."
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Check an app engine imported by another early pytest plugin."""

    _assert_engine_is_not_real_database()


def pytest_collection_finish(session: pytest.Session) -> None:
    """Check again after all test modules have finished importing."""

    _assert_engine_is_not_real_database()

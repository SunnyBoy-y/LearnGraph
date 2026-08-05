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
process-local scratch DB as the default before app code can be imported.  Tests
which deliberately set their own temporary URL before importing app code retain
their per-file isolation; all other tests safely use this fallback scratch DB.
Either way, the real ``backend/data/learngraph.db`` can never be selected by the
default test environment.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="lg-pytest-"))
_REAL_DB_MARKER = "learngraph.db"

# Force isolation rather than setdefault: a developer's shell environment must
# never silently redirect pytest at the production/local-user database.
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest's tmp_path fixtures inside the writable project tree."""

    config.option.basetemp = Path(__file__).parent / ".pytest-tmp"


def _assert_engine_is_not_real_database() -> None:
    """Reject a real-DB engine before test execution can reach ``drop_all``."""

    import sys

    database_module = sys.modules.get("app.core.database")
    if database_module is None:
        return

    engine_url = str(database_module.engine.url)
    if _REAL_DB_MARKER in engine_url.casefold():
        raise pytest.UsageError(
            "pytest engine is bound to the REAL database "
            f"({engine_url}). Refusing to run before any test can execute "
            "drop_all; fix the test database configuration first."
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Check an app engine imported by another early pytest plugin."""

    _assert_engine_is_not_real_database()


def pytest_collection_finish(session: pytest.Session) -> None:
    """Check again after all test modules have finished importing."""

    _assert_engine_is_not_real_database()

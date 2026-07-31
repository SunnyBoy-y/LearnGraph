from __future__ import annotations

from types import SimpleNamespace

from app.api.routers.sandbox import start_bootstrap
from app.core.errors import AppError


def test_workspace_writer_cannot_bootstrap_runtime() -> None:
    context = SimpleNamespace(
        principal=SimpleNamespace(user_id="user-1", is_system_admin=False),
    )
    try:
        start_bootstrap(context, SimpleNamespace())
    except AppError as exc:
        assert exc.status_code == 403
        assert exc.code == "deployment_admin_required"
    else:
        raise AssertionError("non-admin bootstrap unexpectedly succeeded")

"""Run the deployment bootstrap synchronously for CLI/real-runtime verification."""

from __future__ import annotations

import json

from app.core.config import get_settings
from app.services.sandbox_bootstrap import BootstrapJob, SandboxBootstrapService


def main() -> int:
    service = SandboxBootstrapService()
    job = BootstrapJob(id="cli-bootstrap", actor_id="cli")
    service._run_job(job, get_settings())  # noqa: SLF001 - intentional CLI entrypoint
    print(json.dumps(job.to_public(), ensure_ascii=False, indent=2))
    return 0 if job.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Application release identity; retain the original GitHub tag verbatim."""
from pathlib import Path
import re

RELEASE_VERSION = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")


def application_version() -> str:
    location = Path(__file__).resolve()
    # Image: /app/VERSION; source checkout: <repo>/VERSION.
    for parent in (location.parents[2], location.parents[3]):
        candidate = parent / "VERSION"
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            if not RELEASE_VERSION.fullmatch(value):
                raise ValueError("VERSION must match a stable GitHub Release tag (for example v0.5 or v0.5.1)")
            return value
    return "dev"

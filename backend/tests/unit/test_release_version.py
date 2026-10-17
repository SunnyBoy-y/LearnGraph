from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.version import application_version


def test_source_application_version_matches_release_file_verbatim():
    expected = (Path(__file__).resolve().parents[3] / "VERSION").read_text().strip()
    assert application_version() == expected
    assert Settings(_env_file=None).app_version == expected


def test_environment_cannot_silently_spoof_the_packaged_version():
    with pytest.raises(ValueError, match="packaged VERSION"):
        Settings(_env_file=None, app_version="v999.0")

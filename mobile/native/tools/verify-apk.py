"""Verify that the installable APK contains the complete current shared-renderer asset set."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import zipfile

apk = Path(sys.argv[1]).resolve()
with zipfile.ZipFile(apk) as archive:
    source_assets = Path(__file__).resolve().parents[1] / "app/src/main/assets"
    for bridge in ("native-composer.js", "native-composer-state.js", "native-navigation.js", "native-sidebar-activity.js", "native-ui-metrics.js", "native-text-metrics.js"):
        assert archive.read(f"assets/{bridge}") == (source_assets / bridge).read_bytes(), f"Current bridge missing: {bridge}"
    manifest = json.loads(archive.read("assets/renderer/manifest-native.json"))
    missing = []
    mismatched = []
    total = 0
    for relative, expected in manifest["files"].items():
        try:
            data = archive.read(f"assets/renderer/{relative}")
        except KeyError:
            missing.append(relative)
            continue
        total += len(data)
        if hashlib.sha256(data).hexdigest() != expected:
            mismatched.append(relative)
    assert not missing and not mismatched, {"missing": missing, "mismatched": mismatched}
    assert total == manifest["bytes"], "Renderer byte count differs from its manifest"
    native_libraries = [name for name in archive.namelist() if name.startswith("lib/") and name.endswith(".so")]
    engines = [name for name in native_libraries if any(engine in name.lower() for engine in ("chromium", "libxul", "gecko", "xwalk"))]
    assert not engines, f"Unexpected bundled browser engine: {engines}"
    assert "native-app.html" in manifest["files"], "Packaged application entry missing"
    javascript = b"\n".join(archive.read(f"assets/renderer/{name}") for name in manifest["files"] if name.endswith(".js"))
    assert b"showStreamIndicator" in javascript and b"Exit fullscreen" in javascript, "Final renderer protocol/fullscreen patch missing"
    assert b"LearnGraphComposer" in javascript, "Live composer preference bridge missing"
    print(json.dumps({
        "apk": apk.name, "bytes": apk.stat().st_size, "MiB": round(apk.stat().st_size / 1024**2, 2),
        "renderer_files_verified": len(manifest["files"]), "renderer_bytes": total,
        "abis": sorted({name.split("/")[1] for name in native_libraries}), "bundled_browser_engines": len(engines),
        "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
    }, ensure_ascii=False))

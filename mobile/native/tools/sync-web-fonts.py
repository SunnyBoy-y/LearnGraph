"""Generate API-23-compatible static Geist fonts from the Web app's exact Latin WOFF2.
Use an isolated runtime, for example: uv run --with "fonttools[woff]" sync-web-fonts.py.
--dependency-dir can point to a temporary pip --target folder; no global install is needed.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--dependency-dir")
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
if args.dependency_dir:
    sys.path.insert(0, args.dependency_dir)
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
import fontTools

root = Path(__file__).resolve().parents[3]
package = root / "frontend/node_modules/@fontsource-variable/geist"
source = package / "files/geist-latin-wght-normal.woff2"
output = root / "mobile/native/app/src/main/res/font"
weights = {"geist_regular": 400, "geist_medium": 500, "geist_semibold": 600, "geist_650": 650, "geist_bold": 700}
original = TTFont(source, recalcTimestamp=False)
source_cmap = original.getBestCmap()
assert all(ord(char) in source_cmap for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
assert all(ord(char) not in source_cmap for char in "从一个问题开始"), "CJK must retain the phone's own system fallback"
metrics = {"units_per_em": original["head"].unitsPerEm,
           "ascent": original["hhea"].ascent, "descent": -original["hhea"].descent,
           "line_gap": original["hhea"].lineGap,
           "use_typo_metrics": bool(original["OS/2"].fsSelection & 0x80)}
manifest = {
    "source": source.relative_to(root).as_posix(),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "source_package_version": json.loads((package / "package.json").read_text())["version"],
    "fonttools_version": fontTools.__version__,
    "note": "Same Web Latin outlines; CJK uses the Android system fallback. Static instances support API 23.",
    "css_primary_line_metrics": metrics,
    "fonts": {},
}
outputs: list[tuple[Path, bytes]] = []
for name, weight in weights.items():
    # The Web uses 650, which is between Geist's named STAT weight stops.
    instance = instantiateVariableFont(original, {"wght": weight}, inplace=False, updateFontNames=weight != 650)
    if weight == 650:
        for name_id, value in [(2, "SemiBold 650"), (4, "Geist SemiBold 650"), (6, "Geist-SemiBold650"), (17, "SemiBold 650")]:
            instance["name"].setName(value, name_id, 3, 1, 0x409)
    instance.flavor = None
    buffer = io.BytesIO()
    instance.save(buffer, reorderTables=True)
    data = buffer.getvalue()
    check = TTFont(io.BytesIO(data))
    assert "fvar" not in check, "Do not ship an unsupported variable font as the API 23 fallback"
    assert check["OS/2"].usWeightClass == weight
    assert check.getBestCmap() == source_cmap, "Do not lose Latin coverage while instancing"
    assert check["head"].unitsPerEm == metrics["units_per_em"]
    assert [check["hhea"].ascent, -check["hhea"].descent, check["hhea"].lineGap] == [metrics["ascent"], metrics["descent"], metrics["line_gap"]], "Do not rewrite primary baseline metrics"
    assert [check["OS/2"].sTypoAscender, check["OS/2"].sTypoDescender, check["OS/2"].sTypoLineGap] == [original["OS/2"].sTypoAscender, original["OS/2"].sTypoDescender, original["OS/2"].sTypoLineGap]
    assert bool(check["OS/2"].fsSelection & 0x80) == metrics["use_typo_metrics"]
    target = output / (name + ".ttf")
    outputs.append((target, data))
    manifest["fonts"][name] = {"weight": weight, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
outputs.append((root / "mobile/native/app/src/main/assets/licenses/geist-OFL.txt", (package / "LICENSE").read_bytes()))
outputs.append((root / "mobile/native/tools/web-font-source-manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()))
for target, data in outputs:
    if args.check:
        if target.read_bytes() != data:
            raise SystemExit(f"{target.relative_to(root)} is stale; rerun the font sync script")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
total = sum(item["bytes"] for item in manifest["fonts"].values())
print(f"Web Geist fonts {'verified' if args.check else 'synced'}: {len(weights)} static weights, {total:,} bytes before APK compression")

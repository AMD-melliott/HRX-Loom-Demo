#!/usr/bin/env python3
"""
Inline the frontend + catalog into a single HTML file that needs no backend.

    python3 scripts/build_standalone.py --output dist/hrx-loom-demo.html

The result is permanently in REPLAY mode - it has no way to reach a
loom-serve, so it shows published loom-fun data only and says so. That is the
point: it opens on any laptop, with no Python and no network, which is what you
want when the room has neither.

Use app.py instead when you want to measure against a real loom-serve.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def build() -> str:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    catalog = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))

    # Replace the backend fetches with the baked-in payload. The status object
    # is hard-wired to replay so this build can never claim a live reading.
    shim = (
        "const __CATALOG__ = " + json.dumps(catalog, separators=(",", ":")) + ";\n"
        "const __STATUS__ = " + json.dumps({
            "mode": "replay",
            "loom_serve_url": "(standalone build - no backend)",
            "server_models": None,
            "reason": "standalone build has no backend to probe",
            "max_workers": 0,
            "catalog_generated": catalog.get("generated"),
            "catalog_models": catalog.get("model_count"),
            "last_probe": None,
            "probe_running": False,
        }) + ";\n"
        "window.fetch = function(url){\n"
        "  if (String(url).indexOf('/api/catalog') !== -1)\n"
        "    return Promise.resolve({json:()=>Promise.resolve(__CATALOG__)});\n"
        "  if (String(url).indexOf('/api/status') !== -1)\n"
        "    return Promise.resolve({json:()=>Promise.resolve(__STATUS__)});\n"
        "  return Promise.reject(new Error('standalone build: no backend'));\n"
        "};\n"
    )

    html = html.replace(
        '<link rel="stylesheet" href="styles.css">',
        "<style>\n" + css + "\n</style>")
    html = html.replace(
        '<script src="app.js"></script>',
        "<script>\n" + shim + js + "\n</script>")

    # Make the standalone nature visible rather than implied.
    html = html.replace(
        '<span class="prov-note">',
        '<span class="prov-note">Standalone build, no backend — ')
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=pathlib.Path,
                    default=ROOT / "dist" / "hrx-loom-demo.html")
    args = ap.parse_args()

    out = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out, encoding="utf-8")
    print(f"wrote {args.output}  ({len(out) / 1024:.0f} KB, self-contained)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

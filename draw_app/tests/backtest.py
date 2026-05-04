"""Backtest the sketch-to-CAD classifier against the synthetic corpus.

Run:  .venv/bin/python draw_app/tests/backtest.py
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CORPUS = Path(__file__).parent / "sketch_corpus.json"
BASE_URL = "http://127.0.0.1:8080"


def post_generate(strokes: list, canvas_size: int = 600) -> dict:
    body = json.dumps({
        "strokes": [{"points": s} for s in strokes],
        "canvas_width": canvas_size,
        "canvas_height": canvas_size,
        "extrude_height_mm": 10.0,
        "target_size_mm": 60.0,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main() -> int:
    cases = json.loads(CORPUS.read_text())
    passed = 0
    failed = 0
    for case in cases:
        name = case["name"]
        try:
            resp = post_generate(case["strokes"])
        except urllib.error.HTTPError as e:
            print(f"[FAIL] {name}: HTTP {e.code} — {e.read().decode()[:200]}")
            failed += 1
            continue
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failed += 1
            continue

        interps = [i for i in resp["interpretations"] if i["role"] != "skipped"]
        expects = case["expect"]
        ok = True
        if len(interps) != len(expects):
            ok = False
            why = f"got {len(interps)} features, expected {len(expects)}"
        else:
            why = ""
            for got, want in zip(interps, expects):
                if got["kind"] != want["kind"] or got["role"] != want["role"]:
                    ok = False
                    why = f"got ({got['kind']},{got['role']}) want ({want['kind']},{want['role']})"
                    break

        stl_size = len(base64.b64decode(resp["stl_base64"]))
        if ok:
            descs = "  |  ".join(i["description"] for i in interps)
            print(f"[PASS] {name}: {descs} (STL={stl_size}b)")
            passed += 1
        else:
            descs = "  |  ".join(i["description"] for i in interps)
            print(f"[FAIL] {name}: {why}")
            print(f"       got: {descs}")
            failed += 1

    print()
    print(f"Result: {passed} passed, {failed} failed of {passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

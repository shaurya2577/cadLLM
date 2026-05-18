"""Generate a static HTML catalog of every part in cad/.

For each `cad/<part>.py` that has matching artifacts in `generated/`, emits a
card with the PNG thumbnail, part name, STL/STEP/source links, and the file
sizes. Output goes to `generated/catalog.html`.

Run:  .venv/bin/python tools/catalog.py
Open: generated/catalog.html in your browser.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAD = ROOT / "cad"
GEN = ROOT / "generated"


def parts() -> list[dict]:
    """Walk cad/ and pair each part with its artifacts."""
    out: list[dict] = []
    if not CAD.exists():
        return out
    for src in sorted(CAD.glob("*.py")):
        if src.name.startswith("_"):
            continue
        stem = src.stem
        stl = GEN / f"{stem}.stl"
        step = GEN / f"{stem}.step"
        png = GEN / f"{stem}.png"
        if not png.exists():
            continue  # not yet built; skip
        out.append({
            "name": stem,
            "src": src,
            "stl": stl if stl.exists() else None,
            "step": step if step.exists() else None,
            "png": png,
            "doc": _docstring(src),
            "params": _params(src),
        })
    return out


def _docstring(path: Path) -> str:
    text = path.read_text()
    m = re.match(r'\s*"""(.*?)"""', text, re.DOTALL)
    if not m:
        return ""
    return m.group(1).strip().split("\n")[0]


def _params(path: Path) -> list[tuple[str, str, str]]:
    """Pull the UPPER_SNAKE_CASE parameters from the top of the file."""
    out: list[tuple[str, str, str]] = []
    text = path.read_text()
    # Stop at the first line that doesn't look like a parameter or comment/blank.
    for line in text.splitlines():
        # Match: NAME = VALUE  # mm — description
        m = re.match(r'^([A-Z][A-Z0-9_]*)\s*=\s*([^#\n]+?)\s*(?:#\s*(.*))?$', line)
        if m:
            name, val, comment = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
            out.append((name, val, comment))
        # Stop scanning once we hit non-param/non-comment Python (e.g., a function or geometry)
        elif re.match(r'^(def |class |result\s*=|render\s*\()', line.lstrip()):
            break
    return out


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def render_html(items: list[dict]) -> str:
    cards = []
    for it in items:
        png_rel = it["png"].relative_to(GEN)
        src_rel = it["src"].relative_to(ROOT)
        stl_rel = it["stl"].relative_to(GEN) if it["stl"] else None
        step_rel = it["step"].relative_to(GEN) if it["step"] else None

        params_html = ""
        if it["params"]:
            rows = "\n".join(
                f"<tr><td>{html.escape(n)}</td><td>{html.escape(v)}</td>"
                f"<td class='muted'>{html.escape(c)}</td></tr>"
                for n, v, c in it["params"][:12]
            )
            extra = ""
            if len(it["params"]) > 12:
                extra = f"<p class='muted'>… and {len(it['params']) - 12} more parameters in the source.</p>"
            params_html = f"<details><summary>Parameters ({len(it['params'])})</summary><table>{rows}</table>{extra}</details>"

        links = []
        if stl_rel:
            links.append(f"<a href='{stl_rel}'>STL ({_fmt_bytes(it['stl'].stat().st_size)})</a>")
        if step_rel:
            links.append(f"<a href='{step_rel}'>STEP ({_fmt_bytes(it['step'].stat().st_size)})</a>")
        links.append(f"<a href='../{src_rel}'>source</a>")
        links_html = " · ".join(links)

        cards.append(f"""
        <article class="card">
          <a href="{png_rel}" class="thumb"><img src="{png_rel}" alt="{html.escape(it['name'])}"></a>
          <div class="meta">
            <h3>{html.escape(it['name'])}</h3>
            <p class="doc">{html.escape(it['doc'])}</p>
            <p class="links">{links_html}</p>
            {params_html}
          </div>
        </article>
        """)

    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>cadLLM catalog</title>
<style>
:root {{ color-scheme: light dark; --bg:#fafafa; --fg:#222; --muted:#777; --border:#ddd; --panel:#fff; --accent:#2962ff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161616; --fg:#eee; --muted:#999; --border:#333; --panel:#1e1e1e; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
  font: 14px/1.45 system-ui, sans-serif; max-width:1400px; margin:auto; }}
header {{ display:flex; align-items:baseline; gap:14px; margin-bottom:20px; }}
h1 {{ font-size:20px; margin:0; }}
.muted {{ color:var(--muted); font-size:12px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:14px; }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:8px;
  overflow:hidden; display:flex; flex-direction:column; }}
.thumb {{ display:block; background:#1e1e1e; }}
.thumb img {{ display:block; width:100%; height:auto; }}
.meta {{ padding:10px 14px 14px; }}
.meta h3 {{ margin:0 0 4px 0; font-size:14px; font-weight:600; }}
.meta .doc {{ margin:0 0 8px 0; font-size:12px; color:var(--muted); }}
.meta .links {{ margin:0 0 8px 0; font-size:12px; }}
.meta .links a {{ color:var(--accent); text-decoration:none; }}
.meta .links a:hover {{ text-decoration:underline; }}
details {{ font-size:12px; }}
details summary {{ cursor:pointer; color:var(--muted); }}
table {{ border-collapse:collapse; margin-top:4px; font-size:11px; }}
table td {{ padding:2px 6px 2px 0; vertical-align:top; }}
table td:first-child {{ font-family:ui-monospace,monospace; }}
</style></head>
<body>
<header><h1>cadLLM catalog</h1>
<span class="muted">{len(items)} parts · regenerated {when}</span></header>
<section class="grid">
{''.join(cards)}
</section>
</body></html>
"""


def main() -> None:
    GEN.mkdir(exist_ok=True)
    items = parts()
    if not items:
        print("[warn] no parts found in cad/ with matching generated artifacts")
    out = GEN / "catalog.html"
    out.write_text(render_html(items))
    print(f"[ok] wrote {out} ({len(items)} parts)")
    print(f"     open: file://{out.resolve()}")


if __name__ == "__main__":
    main()

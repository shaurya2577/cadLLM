"""Shared render helper for cad/ part scripts.

Adapts the render pattern from `phase0_cube.py` (VTK PNG via subprocess +
matplotlib mesh fallback) so it works for an arbitrary CadQuery shape, not just
a hardcoded cube. Shape is handed to the subprocess via a STEP round-trip — STEP
is the lossless B-rep exchange format and is a useful artifact anyway.

Why a subprocess at all when VTK works in-process on this Linux box?
Belt-and-suspenders. CadQuery's VTK PNG path can segfault (not raise) on headless
machines (notably macOS). Isolating it means a crash returns False here and the
matplotlib fallback takes over — instead of killing the part script.

Outputs land in `generated/<name>.{stl,step,png}` (gitignored).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path("generated")


def render(shape, name: str):
    """Export STL + STEP + PNG for `shape`, with stem `name`.

    `shape` is a cq.Workplane or a cq.Shape. Returns (stl_path, png_path).
    Prints status lines per output. Asserts on empty STL or failed PNG.
    """
    import cadquery as cq

    OUT.mkdir(exist_ok=True)
    stl_path = OUT / f"{name}.stl"
    step_path = OUT / f"{name}.step"
    png_path = OUT / f"{name}.png"

    cq.exporters.export(shape, str(stl_path))
    print(f"[ok] wrote {stl_path} ({stl_path.stat().st_size} bytes)")
    cq.exporters.export(shape, str(step_path))
    print(f"[ok] wrote {step_path} ({step_path.stat().st_size} bytes)")

    ok = False
    try:
        ok = _render_vtk(step_path, png_path)
        if ok:
            print(f"[ok] wrote {png_path} via VTK ({png_path.stat().st_size} bytes)")
    except Exception as e:
        print(f"[warn] VTK render failed ({type(e).__name__}: {e}); falling back to matplotlib")

    if not ok:
        try:
            ok = _render_matplotlib(shape, png_path)
            print(f"[ok] wrote {png_path} via matplotlib fallback ({png_path.stat().st_size} bytes)")
        except Exception as e:
            print(f"[FAIL] both render paths failed: {type(e).__name__}: {e}")

    assert stl_path.stat().st_size > 0, "STL is empty"
    assert ok and png_path.stat().st_size > 0, "PNG render produced nothing"
    print(f"[PASS] {name}: outputs ready. Open {stl_path} in a slicer to confirm.")
    return stl_path, png_path


def _render_vtk(step_path: Path, png_path: Path) -> bool:
    """VTK render in a subprocess (loads the STEP file). Survives segfaults."""
    code = f'''
import os
os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
import cadquery as cq
from cadquery.vis import show
shape = cq.importers.importStep(r"{step_path}")
show(shape, screenshot=r"{png_path}", interact=False)
'''
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        script = f.name
    r = subprocess.run([sys.executable, script], capture_output=True, timeout=60)
    return r.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0


def _render_matplotlib(shape, png_path: Path) -> bool:
    """Mesh tessellation + matplotlib 3D plot. No display dependency."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    val = shape.val() if hasattr(shape, "val") else shape
    verts, tris = val.tessellate(0.1)
    pts = [
        [(verts[a].x, verts[a].y, verts[a].z),
         (verts[b].x, verts[b].y, verts[b].z),
         (verts[c].x, verts[c].y, verts[c].z)]
        for a, b, c in tris
    ]

    # Auto-fit view from the part's bounding box (so this works for any shape).
    xs = [v.x for v in verts]; ys = [v.y for v in verts]; zs = [v.z for v in verts]
    cx, cy, cz = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) * 0.6 or 1.0

    fig = plt.figure(figsize=(4, 4))
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(pts, alpha=0.9, edgecolor="k", linewidths=0.2)
    coll.set_facecolor((0.4, 0.6, 0.85))
    ax.add_collection3d(coll)
    ax.set_xlim(cx - half, cx + half); ax.set_ylim(cy - half, cy + half); ax.set_zlim(cz - half, cz + half)
    ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off()
    fig.savefig(png_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return png_path.exists() and png_path.stat().st_size > 0

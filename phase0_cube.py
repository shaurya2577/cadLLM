"""
Phase 0 reference: a known-good CadQuery cube.

Purpose: a CONTROL. This script has no LLM, no generation, no cleverness. It makes
one cube, exports an STL, and renders a headless PNG. If Claude Code's Phase 0 output
misbehaves, diff against this to tell whether the problem is geometry, the renderer,
or the environment — instead of debugging three unknowns at once.

Run:  python phase0_cube.py
Out:  generated/cube.stl  and  generated/cube.png

Notes on the render step (the fiddly part):
- CadQuery's STL export is rock-solid and dependency-light. That always works.
- PNG rendering goes through VTK, which wants a display. On a headless machine
  (CI, a server, or a Mac running without a window context) you must force VTK
  into offscreen mode BEFORE it initializes, hence the env var at the very top.
- On macOS specifically: if the offscreen path misbehaves, the fallback is to skip
  the VTK PNG and render via a matplotlib 3D plot of the mesh (mesh_png fallback).
  That fallback has no display dependency at all and is the safety net.
"""

import os
# MUST be set before vtk/cadquery.vis import. Forces offscreen GL.
os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkOSOpenGLRenderWindow")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

from pathlib import Path
import cadquery as cq

OUT = Path("generated")
OUT.mkdir(exist_ok=True)

# ---- geometry (the only "CAD" in Phase 0) -------------------------------------
EDGE_LENGTH = 20.0  # mm — exposed as a named param now so Phase 3 has a pattern to follow

cube = cq.Workplane("XY").box(EDGE_LENGTH, EDGE_LENGTH, EDGE_LENGTH)

# ---- STL export (always works) ------------------------------------------------
stl_path = OUT / "cube.stl"
cq.exporters.export(cube, str(stl_path))
print(f"[ok] wrote {stl_path} ({stl_path.stat().st_size} bytes)")


# ---- PNG render: try VTK offscreen, fall back to matplotlib mesh --------------
def render_vtk(shape, path):
    """Preferred: CadQuery's VTK-based PNG export, forced offscreen.

    Run in a SUBPROCESS: VTK offscreen GL can segfault (not raise) on headless
    machines, which would kill the whole script. Isolating it means a crash just
    returns False and we fall back cleanly.
    """
    import subprocess, sys, tempfile
    code = f'''
import os
os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
import cadquery as cq
from cadquery.vis import show
c = cq.Workplane("XY").box({EDGE_LENGTH}, {EDGE_LENGTH}, {EDGE_LENGTH})
show(c, screenshot=r"{path}", interact=False)
'''
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        script = f.name
    r = subprocess.run([sys.executable, script], capture_output=True, timeout=60)
    return r.returncode == 0 and path.exists() and path.stat().st_size > 0


def render_matplotlib(shape, path):
    """Fallback: tessellate to a mesh and plot it. No display dependency at all."""
    import matplotlib
    matplotlib.use("Agg")  # headless backend
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    # Tessellate the B-rep into triangles.
    verts, tris = shape.val().tessellate(0.1)
    pts = [[(v.x, v.y, v.z) for v in (verts[a], verts[b], verts[c])] for a, b, c in tris]

    fig = plt.figure(figsize=(4, 4))
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(pts, alpha=0.9, edgecolor="k", linewidths=0.2)
    coll.set_facecolor((0.4, 0.6, 0.85))
    ax.add_collection3d(coll)
    m = EDGE_LENGTH * 0.6
    ax.set_xlim(-m, m); ax.set_ylim(-m, m); ax.set_zlim(-m, m)
    ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path.exists() and path.stat().st_size > 0


png_path = OUT / "cube.png"
ok = False
try:
    ok = render_vtk(cube, png_path)
    if ok:
        print(f"[ok] wrote {png_path} via VTK ({png_path.stat().st_size} bytes)")
except Exception as e:
    print(f"[warn] VTK render failed ({type(e).__name__}: {e}); falling back to matplotlib")

if not ok:
    try:
        ok = render_matplotlib(cube, png_path)
        print(f"[ok] wrote {png_path} via matplotlib fallback ({png_path.stat().st_size} bytes)")
    except Exception as e:
        print(f"[FAIL] both render paths failed: {type(e).__name__}: {e}")

# ---- acceptance test ----------------------------------------------------------
assert stl_path.stat().st_size > 0, "STL is empty"
assert ok and png_path.stat().st_size > 0, "PNG render produced nothing"
print("[PASS] Phase 0 acceptance: STL + non-empty PNG both produced.")

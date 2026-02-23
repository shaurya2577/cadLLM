"""A cube with a through-hole drilled along Z through the centre."""
import sys; sys.path.insert(0, "cad")  # for _render
from _render import render
import cadquery as cq

# ---- parameters (edit these to tweak) -----------------------------------------
EDGE_LENGTH   = 20.0  # mm — outer cube edge
HOLE_DIAMETER = 5.0   # mm — through-hole diameter

# ---- geometry -----------------------------------------------------------------
# Centered cube at origin, then drill from the top face (>Z). `.hole()` makes it
# a through-hole by default — bottom face gets the matching exit.
result = (
    cq.Workplane("XY")
    .box(EDGE_LENGTH, EDGE_LENGTH, EDGE_LENGTH)
    .faces(">Z").workplane().hole(HOLE_DIAMETER)
)

# ---- export + render ----------------------------------------------------------
render(result, "cube_with_hole")

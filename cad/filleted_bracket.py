"""An L-bracket with filleted corners and four mounting holes."""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq

# ---- parameters ---------------------------------------------------------------
BASE_LENGTH   = 60.0  # mm — long edge of the base plate
BASE_WIDTH    = 40.0  # mm — short edge of the base plate
THICKNESS     = 5.0   # mm — uniform wall thickness
WALL_HEIGHT   = 30.0  # mm — height of the vertical wall (rises in +Z)
FILLET_RADIUS = 4.0   # mm — outer corner fillet on the base plate
HOLE_DIAMETER = 4.0   # mm — mounting-hole diameter
HOLE_INSET    = 6.0   # mm — distance from hole centre to nearest base edge

# ---- geometry -----------------------------------------------------------------
# Asymmetric part → base sits ON the XY plane, Z is up. Build the base, fillet the
# top corners of the base (which are the "outer" corners when looking down), drill
# the four mount holes, then add the vertical wall as a separate solid and union.
base = (
    cq.Workplane("XY")
    .box(BASE_LENGTH, BASE_WIDTH, THICKNESS, centered=(True, True, False))
    .edges("|Z").fillet(FILLET_RADIUS)  # vertical edges = corner fillets seen from above
    .faces(">Z").workplane()
    .rect(BASE_LENGTH - 2 * HOLE_INSET, BASE_WIDTH - 2 * HOLE_INSET, forConstruction=True)
    .vertices().hole(HOLE_DIAMETER)
)

# Vertical wall: sits on the back edge of the base (at +X/2 or -X/2 — pick -X side
# so the bracket "opens" toward +X). Wall is centred in Y.
wall = (
    cq.Workplane("YZ")
    .workplane(offset=-BASE_LENGTH / 2 + THICKNESS / 2)
    .box(BASE_WIDTH, WALL_HEIGHT, THICKNESS, centered=(True, False, True))
    .translate((0, 0, THICKNESS))  # wall starts at the top of the base
)

result = base.union(wall)

# ---- export + render ----------------------------------------------------------
render(result, "filleted_bracket")

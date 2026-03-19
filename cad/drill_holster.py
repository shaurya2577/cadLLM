"""Pegboard-mounted holster for a cordless drill (DeWalt 20V foot).

Topology: a vertical back plate that screws to the pegboard, plus a horizontal
"tray" extending forward. The drill's battery foot slides into the tray from
the front; the drill body protrudes up. A small lip at the front of the tray
floor stops the drill from sliding back out.

The user's hard constraints are the 62mm pocket width and 50mm screw spacing;
everything else here is reasonable defaults you can edit at the top of the file.
"""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq

# ---- parameters ---------------------------------------------------------------
# Battery pocket — the load-bearing dimension
POCKET_WIDTH    = 62.0   # mm — interior width across the battery foot (DeWalt 20V)
POCKET_DEPTH    = 35.0   # mm — how far the foot slides INTO the tray (Y dimension)
POCKET_HEIGHT   = 30.0   # mm — wall height around the pocket (Z)

# Back plate
BACK_WIDTH      = 90.0   # mm
BACK_HEIGHT     = 70.0   # mm
BACK_THICKNESS  = 6.0    # mm — beefy so the screw holes don't tear out
WALL_THICKNESS  = 6.0    # mm — pocket walls and floor

# Mounting — two countersunk holes, 50mm centre-to-centre, vertical pair
SCREW_HOLE_DIA      = 4.5   # mm — clearance for #8 / M4
COUNTERSINK_DIA     = 9.0   # mm
COUNTERSINK_DEPTH   = 3.0   # mm — head recess depth from the wall-facing side
SCREW_SPACING       = 50.0  # mm — centre-to-centre

# Retention lip at the front of the tray
FRONT_LIP_HEIGHT = 10.0  # mm
FRONT_LIP_THICK  = 5.0   # mm

# Corner softening (fillet radius must be < adjacent face thickness / 2,
# otherwise BRep_API: command not done. Hard lesson with the 6mm back plate.)
# Corner softening (small enough to fit on the 6mm thickness)
CORNER_FILLET   = 2.5    # mm

# ---- geometry -----------------------------------------------------------------
# Coords: +Y = away from wall, +Z = up. Wall sits at Y=0..BACK_THICKNESS, drill
# pocket extends in -Y... wait, simpler to have the tray extend in +Y FROM the
# back plate. Back plate at Y in [-BACK_THICKNESS, 0], tray at Y in [0, POCKET_DEPTH+WALL].

# 1) Back plate — fillet the vertical corners pre-union.
back = (
    cq.Workplane("XY")
    .box(BACK_WIDTH, BACK_THICKNESS, BACK_HEIGHT, centered=(True, False, False))
    .translate((0, -BACK_THICKNESS, 0))
    .edges("|Z").fillet(CORNER_FILLET)
)

# 2) Pocket tray. Outer envelope = pocket + walls + floor. Built as a solid box
#    then the interior cavity is cut. Pocket opens at the TOP (drop the drill
#    foot in from above) and at the FRONT (slide it in from the front).
tray_outer_w = POCKET_WIDTH + 2 * WALL_THICKNESS  # X
tray_outer_d = POCKET_DEPTH                       # Y (no back wall — back plate is the back)
tray_outer_h = POCKET_HEIGHT + WALL_THICKNESS     # Z (floor + walls)

tray_outer = (
    cq.Workplane("XY")
    .box(tray_outer_w, tray_outer_d, tray_outer_h, centered=(True, False, False))
)

# Interior cavity — same width as POCKET_WIDTH, full depth (open at front), open
# at top (extend cavity above the wall height).
cavity = (
    cq.Workplane("XY")
    .box(POCKET_WIDTH, POCKET_DEPTH + 1, POCKET_HEIGHT + 1,
         centered=(True, False, False))
    .translate((0, -0.5, WALL_THICKNESS))
)

tray = tray_outer.cut(cavity)

# 3) Front retention lip — a small ridge on the floor at the front edge of the
#    tray (Y = POCKET_DEPTH end). Prevents the drill from sliding back out.
lip = (
    cq.Workplane("XY")
    .box(POCKET_WIDTH, FRONT_LIP_THICK, FRONT_LIP_HEIGHT,
         centered=(True, False, False))
    .translate((0, POCKET_DEPTH - FRONT_LIP_THICK, WALL_THICKNESS))
)
tray = tray.union(lip)

# 4) Union with the back plate.
body = back.union(tray)

# 5) Mounting holes — countersunk on the WALL-FACING side (-Y), so the screw head
#    recesses into the plate and the holster sits flat against the pegboard.
hole_z_lower = (BACK_HEIGHT - SCREW_SPACING) / 2
hole_z_upper = hole_z_lower + SCREW_SPACING

for hz in (hole_z_lower, hole_z_upper):
    # Through-hole for screw shank (along Y-axis, through back plate)
    shank = (
        cq.Workplane("XZ")
        .center(0, hz)
        .circle(SCREW_HOLE_DIA / 2)
        .extrude(BACK_THICKNESS + 2)
        .translate((0, -BACK_THICKNESS - 1, 0))
    )
    body = body.cut(shank)
    # Countersink — open toward the wall (-Y direction)
    csink = (
        cq.Workplane("XZ")
        .center(0, hz)
        .circle(COUNTERSINK_DIA / 2)
        .extrude(COUNTERSINK_DEPTH)
        .translate((0, -BACK_THICKNESS, 0))
    )
    body = body.cut(csink)

result = body

# ---- export + render ----------------------------------------------------------
render(result, "drill_holster")

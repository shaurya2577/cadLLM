"""End cap that slides over a hollow chair leg to protect the floor."""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq

# ---- parameters ---------------------------------------------------------------
LEG_OUTER_DIAMETER = 22.0  # mm — measured outside diameter of the chair leg tube
SOCKET_DEPTH       = 25.0  # mm — how far the cap slides up the leg
CAP_BASE_DIAMETER  = 30.0  # mm — outer diameter at the floor side (spreads load)
WALL_THICKNESS     = 2.0   # mm — wall around the leg socket
BOTTOM_THICKNESS   = 3.0   # mm — solid floor of the cap
BOTTOM_FILLET      = 1.5   # mm — rounded bottom edge so it doesn't catch carpet

# ---- geometry -----------------------------------------------------------------
# The cap is a short cylinder with a blind hole drilled in the top. Outside
# diameter is CAP_BASE_DIAMETER, inside socket diameter equals the leg's outer
# diameter (snug press-fit, can be sanded if too tight). Total height = socket
# depth + bottom thickness.
TOTAL_HEIGHT = SOCKET_DEPTH + BOTTOM_THICKNESS

result = (
    cq.Workplane("XY")
    .circle(CAP_BASE_DIAMETER / 2)
    .extrude(TOTAL_HEIGHT)
    # Drill the leg socket from the top face downward, leaving BOTTOM_THICKNESS
    # of solid material at the bottom.
    .faces(">Z").workplane()
    .hole(LEG_OUTER_DIAMETER, depth=SOCKET_DEPTH)
    # Round the bottom outer edge — the edge at z=0 on the perimeter.
    .edges("<Z").fillet(BOTTOM_FILLET)
)

# ---- export + render ----------------------------------------------------------
render(result, "chair_leg_cap")

"""A hex nut: hexagonal prism with a central through-hole."""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq

# ---- parameters ---------------------------------------------------------------
ACROSS_FLATS  = 17.0  # mm — wrench size (distance between parallel hex faces)
THICKNESS     = 8.0   # mm — nut height
HOLE_DIAMETER = 10.0  # mm — central through-hole (would be the thread minor dia)

# ---- geometry -----------------------------------------------------------------
# `polygon(6, d)` builds a hex inscribed in a circle of diameter d. Across-flats is
# what wrenches grip; for a regular hex, across-corners = across-flats * 2/√3.
import math
ACROSS_CORNERS = ACROSS_FLATS * 2 / math.sqrt(3)

result = (
    cq.Workplane("XY")
    .polygon(6, ACROSS_CORNERS)
    .extrude(THICKNESS)
    .faces(">Z").workplane().hole(HOLE_DIAMETER)
    .translate((0, 0, -THICKNESS / 2))  # centre the nut on the origin in Z
)

# ---- export + render ----------------------------------------------------------
render(result, "hex_nut")

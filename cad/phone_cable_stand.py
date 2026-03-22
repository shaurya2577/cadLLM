"""Desktop phone-charging stand with three cable-tuck channels behind."""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq
import math

# ---- parameters ---------------------------------------------------------------
# Footprint
BASE_WIDTH    = 110.0  # mm — overall X
BASE_DEPTH    = 90.0   # mm — overall Y (front-to-back)
BASE_HEIGHT   = 35.0   # mm — overall Z

# Phone cradle slot — phone is roughly 80mm wide × 10mm thick (with case).
# Updated for "chunky case" feedback: slot bumped up for breathing room.
PHONE_WIDTH       = 85.0   # mm — interior slot width (X) — leaves a few mm slop
PHONE_THICKNESS   = 14.0   # mm — slot thickness (Y) — fits a phone with chunky case
PHONE_SLOT_DEPTH  = 25.0   # mm — how far into the body the slot goes (Z)
PHONE_TILT_DEG    = 10.0   # degrees — slot tilts back so phone leans

# Cable notch at the bottom of the phone slot (charging cable comes up from below)
CABLE_NOTCH_W = 16.0  # mm
CABLE_NOTCH_D = 10.0  # mm — front-to-back

# Cable tuck channel behind the phone (single wide channel, replaces the
# previous trio after iteration feedback that three was overkill).
CABLE_CHANNEL_W     = 20.0  # mm — wider single channel down the centre
CABLE_CHANNEL_D     = 8.0   # mm — front-to-back
CABLE_CHANNEL_DEPTH = 18.0  # mm — how deep into the top surface (Z)

# Aesthetic
CORNER_FILLET    = 4.0    # mm — outer vertical edges (rounded so it doesn't look like a brick)
TOP_FRONT_FILLET = 2.0    # mm — round the top front edge so it doesn't look sharp

# ---- geometry -----------------------------------------------------------------
# Coords: phone slot is at the FRONT of the base. Cable channels are behind the slot.
# Front = -Y side. Origin centred in X, base on Z=0.
# Tilt: the phone slot is rotated about the X-axis so its top end leans toward +Y (back).

# 1) Base block with rounded vertical outer edges.
base = (
    cq.Workplane("XY")
    .box(BASE_WIDTH, BASE_DEPTH, BASE_HEIGHT, centered=(True, True, False))
    .edges("|Z").fillet(CORNER_FILLET)
)

# 2) Phone slot — a tilted box subtracted from the top. Built upright first, then
#    rotated about X-axis at its bottom edge so the slot leans back.
#    Position the slot near the front (small +Y offset from the front face).
SLOT_FRONT_OFFSET = 14.0   # mm — distance from front face to centre of slot
slot_x_centre = 0.0
slot_y_centre = -BASE_DEPTH / 2 + SLOT_FRONT_OFFSET  # in front half
slot_top_z = BASE_HEIGHT + 5  # extend above top so the slot is open

slot_height = PHONE_SLOT_DEPTH + 10  # slot length along its own axis

# Build the slot box centred on its own bottom-front edge (so rotation about X
# pivots it neatly).
slot = (
    cq.Workplane("XY")
    .box(PHONE_WIDTH, PHONE_THICKNESS, slot_height, centered=(True, True, False))
    # Rotate about the X-axis so the TOP end leans back (+Y).
    .rotate((0, 0, 0), (1, 0, 0), -PHONE_TILT_DEG)
    # Position it so the slot's bottom sits at slot_top_z - PHONE_SLOT_DEPTH at
    # the slot centre.
    .translate((slot_x_centre, slot_y_centre, BASE_HEIGHT - PHONE_SLOT_DEPTH))
)
base = base.cut(slot)

# 3) Cable pass-through notch at the bottom of the phone slot (lets the charging
#    cable come up through the base from underneath the phone).
notch = (
    cq.Workplane("XY")
    .box(CABLE_NOTCH_W, CABLE_NOTCH_D, BASE_HEIGHT + 2, centered=(True, True, False))
    .translate((slot_x_centre, slot_y_centre, -1))
)
base = base.cut(notch)

# 4) Single wide cable-tuck channel behind the phone slot (top-down cut).
channel_centre_y = BASE_DEPTH / 2 - 14.0  # back-of-base, leave a small margin
ch = (
    cq.Workplane("XY")
    .box(CABLE_CHANNEL_W, CABLE_CHANNEL_D, CABLE_CHANNEL_DEPTH + 1,
         centered=(True, True, False))
    .translate((0, channel_centre_y, BASE_HEIGHT - CABLE_CHANNEL_DEPTH))
)
base = base.cut(ch)

# 5) Round the top front edge so it doesn't look sharp.
#    Select the edge along X at minimum Y (front) and maximum Z (top).
base = base.edges("|X and >Z").edges("<Y").fillet(TOP_FRONT_FILLET)

result = base

# ---- export + render ----------------------------------------------------------
render(result, "phone_cable_stand")

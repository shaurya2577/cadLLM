"""Desktop phone-charging stand with three cable-tuck channels behind."""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq
import math

# ---- parameters ---------------------------------------------------------------
BASE_WIDTH    = 110.0  # mm
BASE_DEPTH    = 90.0   # mm
BASE_HEIGHT   = 35.0   # mm

PHONE_WIDTH       = 80.0
PHONE_THICKNESS   = 12.0
PHONE_SLOT_DEPTH  = 25.0
PHONE_TILT_DEG    = 10.0

CABLE_NOTCH_W = 16.0
CABLE_NOTCH_D = 10.0

# Three cable channels behind the phone slot.
N_CABLE_CHANNELS    = 3
CABLE_CHANNEL_W     = 6.0
CABLE_CHANNEL_D     = 8.0
CABLE_CHANNEL_DEPTH = 18.0
CABLE_CHANNEL_PITCH = 18.0

CORNER_FILLET = 4.0

# ---- geometry -----------------------------------------------------------------
base = (
    cq.Workplane("XY")
    .box(BASE_WIDTH, BASE_DEPTH, BASE_HEIGHT, centered=(True, True, False))
    .edges("|Z").fillet(CORNER_FILLET)
)

SLOT_FRONT_OFFSET = 14.0
slot_x_centre = 0.0
slot_y_centre = -BASE_DEPTH / 2 + SLOT_FRONT_OFFSET
slot_height = PHONE_SLOT_DEPTH + 10

slot = (
    cq.Workplane("XY")
    .box(PHONE_WIDTH, PHONE_THICKNESS, slot_height, centered=(True, True, False))
    .rotate((0, 0, 0), (1, 0, 0), -PHONE_TILT_DEG)
    .translate((slot_x_centre, slot_y_centre, BASE_HEIGHT - PHONE_SLOT_DEPTH))
)
base = base.cut(slot)

notch = (
    cq.Workplane("XY")
    .box(CABLE_NOTCH_W, CABLE_NOTCH_D, BASE_HEIGHT + 2, centered=(True, True, False))
    .translate((slot_x_centre, slot_y_centre, -1))
)
base = base.cut(notch)

channel_centre_y = BASE_DEPTH / 2 - 14.0
total_pitch = CABLE_CHANNEL_PITCH * (N_CABLE_CHANNELS - 1)
for i in range(N_CABLE_CHANNELS):
    cx = -total_pitch / 2 + i * CABLE_CHANNEL_PITCH
    ch = (
        cq.Workplane("XY")
        .box(CABLE_CHANNEL_W, CABLE_CHANNEL_D, CABLE_CHANNEL_DEPTH + 1,
             centered=(True, True, False))
        .translate((cx, channel_centre_y, BASE_HEIGHT - CABLE_CHANNEL_DEPTH))
    )
    base = base.cut(ch)

result = base
render(result, "phone_cable_stand")

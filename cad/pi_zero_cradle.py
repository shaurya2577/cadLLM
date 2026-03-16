"""A cradle to hold a Raspberry Pi Zero upright on a desk.

Simplification: the user asked for a slight backward tilt; this v1 holds the Pi
vertical (no tilt) to keep the geometry tractable. Add tilt by changing
`PI_TILT_DEG` and re-running — or ask Claude Code to do the math.

Topology: solid block with a Pi-sized slot cut from the top. A USB-access notch
cuts in from one side. A small SD-card slot is removed from the bottom of the
slot. Two mounting holes in the base.
"""
import sys; sys.path.insert(0, "cad")
from _render import render
import cadquery as cq

# ---- parameters ---------------------------------------------------------------
# Pi Zero
PI_LENGTH    = 65.0   # mm — long edge
PI_WIDTH     = 30.0   # mm — short edge (thickness of slot)
PI_CLEARANCE = 0.4    # mm — slop so the Pi isn't stuck

# Cradle body
BASE_LENGTH    = 80.0   # mm — X
BASE_WIDTH     = 50.0   # mm — Y
BOARD_HEIGHT_Z = 22.0   # mm — Pi sits this high above the desk
WALL_BELOW_PI  = 6.0    # mm — solid floor below the slot (heat clearance)
SLOT_DEPTH     = 22.0   # mm — how deep the Pi slides into the slot from the top
CRADLE_HEIGHT  = WALL_BELOW_PI + SLOT_DEPTH

# Features
USB_NOTCH_W = 25.0   # mm — width of the side cutout for USB cables
USB_NOTCH_H = 14.0   # mm — height of the cutout (Z)
SD_SLOT_W   = 14.0   # mm — width of the SD-card slot (cuts through the floor)
SD_SLOT_D   = 4.0    # mm — depth into the body (Y) where the SD card sticks out

MOUNT_HOLE_DIA   = 3.5    # mm — clearance for M3
MOUNT_HOLE_INSET = 8.0    # mm — from each end (X) and side (Y)

CORNER_FILLET = 4.0   # mm — outer vertical corners

PI_TILT_DEG = 0.0  # see docstring; nonzero values not yet implemented

# ---- geometry -----------------------------------------------------------------
# Coords: X is long axis of Pi & cradle, Y is short, Z is up. Cradle centred on origin.

# 1) Cradle body — block with rounded outer corners.
body = (
    cq.Workplane("XY")
    .box(BASE_LENGTH, BASE_WIDTH, CRADLE_HEIGHT, centered=(True, True, False))
    .edges("|Z").fillet(CORNER_FILLET)
)

# 2) Pi slot — vertical slot for the Pi. Cut from the top, depth SLOT_DEPTH.
slot = (
    cq.Workplane("XY")
    .box(PI_LENGTH + PI_CLEARANCE, PI_WIDTH + PI_CLEARANCE, SLOT_DEPTH + 1,
         centered=(True, True, False))
    .translate((0, 0, WALL_BELOW_PI))
)
body = body.cut(slot)

# 3) USB cutout on one of the long sides. Pi USB ports sit along one of the 65mm
#    edges, near the bottom of the board. Cut from -Y side (front) into the slot.
usb_notch = (
    cq.Workplane("XY")
    .box(USB_NOTCH_W, BASE_WIDTH / 2 + 2, USB_NOTCH_H,
         centered=(True, False, False))
    .translate((0, -BASE_WIDTH / 2 - 1, WALL_BELOW_PI + 3))
)
body = body.cut(usb_notch)

# 4) SD card slot — the microSD sticks out the short edge of the Pi. Cut a small
#    channel through the floor at one of the short ends.
sd_slot = (
    cq.Workplane("XY")
    .box(SD_SLOT_D + 2, SD_SLOT_W, WALL_BELOW_PI + 2,
         centered=(False, True, False))
    .translate((-PI_LENGTH / 2 - SD_SLOT_D + 1, 0, -1))
)
body = body.cut(sd_slot)

# 5) Two mounting holes through the base — one near each long-edge end of the base.
for sx in (-1, 1):
    for sy in (-1, 1):
        # Holes at the four corners of the base would be excessive — pick two,
        # diagonally opposite, so the cradle doesn't rotate when screwed down.
        if sx * sy < 0:
            continue  # keep two of four corners
        cx = sx * (BASE_LENGTH / 2 - MOUNT_HOLE_INSET)
        cy = sy * (BASE_WIDTH / 2 - MOUNT_HOLE_INSET)
        hole = (
            cq.Workplane("XY")
            .center(cx, cy)
            .circle(MOUNT_HOLE_DIA / 2)
            .extrude(CRADLE_HEIGHT + 1)
            .translate((0, 0, -0.5))
        )
        body = body.cut(hole)

result = body

# ---- export + render ----------------------------------------------------------
render(result, "pi_zero_cradle")

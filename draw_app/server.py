"""Sketch-to-CAD server, operation-list edition.

The model is now built as an ordered list of operations rather than one-shot
strokes → extrude. Each op is one of: sketch_extrude, sketch_cut, hole,
fillet, chamfer, set_height, pattern_linear, mirror. The frontend sends
the current op list; the backend executes them in order and returns the
STL plus a per-op success/failure report.

The old /generate endpoint stays for backward compat with the corpus
backtest — it wraps a single sketch_extrude op around the request.

Endpoints:
  GET  /          → the SPA
  POST /generate  → backward-compat one-shot extrude
  POST /build     → run an op-list, return STL + per-op feedback
  POST /parse     → translate a text prompt to an op list (no execution)
  POST /save      → write a runnable cad/<name>.py for the current ops
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Tuple, Union

import cadquery as cq
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator


def _load_env() -> None:
    """Tiny .env loader; sets keys not already in os.environ. No external dep."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
STATIC = ROOT / "static"
CAD_DIR = PROJECT_ROOT / "cad"
sys.setrecursionlimit(5000)

app = FastAPI(title="cadLLM sketch-to-CAD")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

Point = Tuple[float, float]


# ============================================================================
# Operation model
# ============================================================================

class Stroke(BaseModel):
    points: List[Point]
    # Optional per-stroke annotations from the user — "this circle is 10mm dia"
    # or "this rect is 30mm wide." Overrides the pixel-derived size.
    annotation: Optional["StrokeAnnotation"] = None
    # Construction strokes don't contribute to the extrusion; they serve as
    # alignment scaffolding (axis of revolution, symmetry line, datum).
    construction: bool = False


class StrokeAnnotation(BaseModel):
    kind: Literal["diameter", "width", "height", "size"]  # what dimension the value refers to
    value_mm: float = Field(gt=0)


class SketchExtrudeOp(BaseModel):
    op: Literal["sketch_extrude"] = "sketch_extrude"
    strokes: List[Stroke]
    height_mm: float = Field(10.0, gt=0)
    plane: Literal["XY", "top", "bottom"] = "XY"
    # Boolean operation against the existing model (Fusion's "operation" dropdown).
    # new_body = ignore the existing model, replace it; join = union;
    # cut = subtract the extruded volume; intersect = keep only the overlap.
    mode: Literal["new_body", "join", "cut", "intersect"] = "join"


class SketchCutOp(BaseModel):
    op: Literal["sketch_cut"] = "sketch_cut"
    strokes: List[Stroke]
    depth_mm: Optional[float] = None  # None = through
    plane: Literal["XY", "top", "bottom"] = "top"


class HoleOp(BaseModel):
    op: Literal["hole"] = "hole"
    x_mm: float = 0.0
    y_mm: float = 0.0
    diameter_mm: float = Field(5.0, gt=0)
    depth_mm: Optional[float] = None  # None = through
    plane: Literal["XY", "top", "bottom"] = "top"


class FilletOp(BaseModel):
    op: Literal["fillet"] = "fillet"
    radius_mm: float = Field(2.0, gt=0)
    target: Literal["all", "top", "bottom", "vertical"] = "all"


class ChamferOp(BaseModel):
    op: Literal["chamfer"] = "chamfer"
    distance_mm: float = Field(1.0, gt=0)
    target: Literal["all", "top", "bottom", "vertical"] = "all"


class SetHeightOp(BaseModel):
    """Mutate the most recent sketch_extrude op's height_mm."""
    op: Literal["set_height"] = "set_height"
    value_mm: float = Field(gt=0)


class PatternLinearOp(BaseModel):
    """Replicate the most recent eligible op (hole / sketch_cut) N times along an axis."""
    op: Literal["pattern_linear"] = "pattern_linear"
    axis: Literal["x", "y"] = "x"
    count: int = Field(2, ge=2)
    spacing_mm: float = Field(20.0, gt=0)


class MirrorOp(BaseModel):
    """Mirror the current solid across a plane."""
    op: Literal["mirror"] = "mirror"
    plane: Literal["YZ", "XZ"] = "YZ"


class RevolveOp(BaseModel):
    """Revolve a profile stroke around an axis to produce a solid of revolution.

    Profile = the largest non-construction closed stroke. Axis = the first
    construction stroke OR the Y axis if none is provided. Sweep angle is
    in degrees (default 360 = full revolution).
    """
    op: Literal["revolve"] = "revolve"
    strokes: List[Stroke]
    angle_deg: float = Field(360.0, gt=0, le=360.0)
    axis: Literal["X", "Y", "X_canvas", "Y_canvas"] = "Y_canvas"


class ShellOp(BaseModel):
    """Hollow out a solid to a given wall thickness, optionally removing top/bottom faces."""
    op: Literal["shell"] = "shell"
    thickness_mm: float = Field(2.0, gt=0)
    remove: Literal["top", "bottom", "none"] = "top"


class CircularPatternOp(BaseModel):
    """Pattern the most recent hole around a circular path."""
    op: Literal["circular_pattern"] = "circular_pattern"
    count: int = Field(4, ge=2)
    radius_mm: float = Field(20.0, gt=0)
    cx_mm: float = 0.0
    cy_mm: float = 0.0


Op = Annotated[
    Union[
        SketchExtrudeOp, SketchCutOp, HoleOp, FilletOp, ChamferOp,
        SetHeightOp, PatternLinearOp, MirrorOp,
        RevolveOp, ShellOp, CircularPatternOp,
    ],
    Field(discriminator="op"),
]


# ---- request/response shapes -------------------------------------------------

class BuildRequest(BaseModel):
    operations: List[Op]
    canvas_width: float = Field(600.0, gt=0)
    canvas_height: float = Field(600.0, gt=0)
    target_size_mm: float = Field(60.0, gt=0)

    @field_validator("canvas_width", "canvas_height", "target_size_mm")
    @classmethod
    def finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v


class OpFeedback(BaseModel):
    index: int
    op: str
    status: Literal["ok", "error", "skipped"]
    summary: str  # human-readable explanation


class BuildResponse(BaseModel):
    stl_base64: str
    feedback: List[OpFeedback]
    # Per-stroke interpretations for any sketch ops in the chain.
    stroke_interpretations: List["StrokeInterpretation"] = []


class StrokeInterpretation(BaseModel):
    op_index: int          # which sketch op this stroke belongs to
    stroke_index: int      # index within that op's strokes
    kind: str              # circle | rect | polygon
    role: str              # outer | hole | additive | skipped
    description: str


class ParseRequest(BaseModel):
    text: str
    canvas_width: float = 600.0
    canvas_height: float = 600.0
    use_llm: bool = True    # try the LLM fallback for unparsed lines


class ParseResponse(BaseModel):
    operations: List[Op]
    unparsed: List[str]    # lines that didn't match any pattern
    llm_used: bool = False  # true if the LLM helped translate any line


class InspectRequest(BuildRequest):
    pass


class InspectResponse(BaseModel):
    watertight: bool
    is_volume: bool         # bounded volume (volume > 0)
    volume_mm3: float
    surface_area_mm2: float
    bbox_mm: List[float]    # [dx, dy, dz]
    n_triangles: int
    issues: List[str]       # human-readable warnings


class ExportRequest(BuildRequest):
    format: Literal["stl", "step", "3mf", "glb", "obj"] = "stl"


class PartsListResponse(BaseModel):
    parts: List[dict]


class OpenPartResponse(BaseModel):
    name: str
    docstring: str
    parameters: List[dict]   # [{name, value, comment}]
    source: str


class SaveRequest(BuildRequest):
    save_as: str = Field(min_length=1, max_length=64)

    @field_validator("save_as")
    @classmethod
    def snake_case(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError("name must be snake_case starting with a letter")
        return v


class SaveResponse(BaseModel):
    path: str
    bytes_written: int


# ---- legacy /generate request (one sketch, one extrude) ---------------------

class GenerateRequest(BaseModel):
    strokes: List[Stroke]
    canvas_width: float
    canvas_height: float
    extrude_height_mm: float = Field(10.0, gt=0)
    target_size_mm: float = Field(60.0, gt=0)
    force_primitive: Optional[Literal["circle", "rect", "polygon"]] = None


class GenerateResponse(BaseModel):
    stl_base64: str
    interpretations: List[StrokeInterpretation]


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    try:
        return (STATIC / "index.html").read_text()
    except FileNotFoundError:
        raise HTTPException(500, "frontend not found (static/index.html missing)")


@app.post("/build", response_model=BuildResponse)
def build(req: BuildRequest) -> BuildResponse:
    if not req.operations:
        raise HTTPException(400, "no operations")
    model, feedback, interps = _execute_ops(req)
    if model is None:
        raise HTTPException(400, "no geometry produced — the first op must build a body")
    stl_bytes = _export_stl(model)
    return BuildResponse(
        stl_base64=base64.b64encode(stl_bytes).decode("ascii"),
        feedback=feedback,
        stroke_interpretations=interps,
    )


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    """Legacy shim. Wraps a single sketch_extrude op around the request."""
    # Apply force_primitive globally (it overrides the classifier).
    sketch_op = SketchExtrudeOp(
        strokes=req.strokes, height_mm=req.extrude_height_mm, plane="XY"
    )
    build_req = BuildRequest(
        operations=[sketch_op],
        canvas_width=req.canvas_width,
        canvas_height=req.canvas_height,
        target_size_mm=req.target_size_mm,
    )
    model, _feedback, interps = _execute_ops(build_req, force_primitive=req.force_primitive)
    if model is None:
        raise HTTPException(400, "no geometry produced")
    stl_bytes = _export_stl(model)
    # Re-shape interpretations to the legacy shape (stroke_index becomes index).
    legacy = [
        StrokeInterpretation(
            op_index=0, stroke_index=i.stroke_index,
            kind=i.kind, role=i.role, description=i.description,
        )
        for i in interps
    ]
    # Provide a flat-ish interpretation list with the old `index` field.
    return GenerateResponse(
        stl_base64=base64.b64encode(stl_bytes).decode("ascii"),
        interpretations=legacy,
    )


@app.post("/parse", response_model=ParseResponse)
def parse_text(req: ParseRequest) -> ParseResponse:
    ops, unparsed = parse_prompt(req.text)
    llm_used = False
    if unparsed and req.use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        llm_ops, still_unparsed = _llm_parse(unparsed)
        ops.extend(llm_ops)
        unparsed = still_unparsed
        llm_used = bool(llm_ops)
    return ParseResponse(operations=ops, unparsed=unparsed, llm_used=llm_used)


@app.post("/save", response_model=SaveResponse)
def save(req: SaveRequest) -> SaveResponse:
    model, _feedback, _interps = _execute_ops(req)
    if model is None:
        raise HTTPException(400, "no geometry produced")
    code = _emit_script(req, req.save_as)
    CAD_DIR.mkdir(parents=True, exist_ok=True)
    out = CAD_DIR / f"{req.save_as}.py"
    if out.exists():
        i = 2
        while (CAD_DIR / f"{req.save_as}_{i}.py").exists():
            i += 1
        out = CAD_DIR / f"{req.save_as}_{i}.py"
    out.write_text(code)
    return SaveResponse(path=str(out.relative_to(PROJECT_ROOT)), bytes_written=len(code))


@app.post("/inspect", response_model=InspectResponse)
def inspect(req: InspectRequest) -> InspectResponse:
    """Run trimesh on the built model and report basic geometric health."""
    import trimesh
    model, _feedback, _interps = _execute_ops(req)
    if model is None:
        raise HTTPException(400, "no geometry produced")
    stl_bytes = _export_stl(model)
    mesh = trimesh.load(io_stream(stl_bytes, ".stl"), file_type="stl")
    issues: list[str] = []
    if not mesh.is_watertight:
        issues.append("mesh is not watertight (holes or open boundaries)")
    if not mesh.is_volume:
        issues.append("mesh does not bound a positive volume")
    if mesh.bounding_box.extents.min() <= 1e-6:
        issues.append("zero-thickness axis (degenerate bounding box)")
    return InspectResponse(
        watertight=bool(mesh.is_watertight),
        is_volume=bool(mesh.is_volume),
        volume_mm3=float(mesh.volume),
        surface_area_mm2=float(mesh.area),
        bbox_mm=[float(x) for x in mesh.bounding_box.extents],
        n_triangles=int(len(mesh.faces)),
        issues=issues,
    )


@app.post("/export")
def export(req: ExportRequest):
    """Export the built model in the requested format. Returns raw bytes."""
    model, _feedback, _interps = _execute_ops(req)
    if model is None:
        raise HTTPException(400, "no geometry produced")
    fmt = req.format.lower()
    mime = {
        "stl": "model/stl",
        "step": "application/step",
        "3mf": "model/3mf",
        "glb": "model/gltf-binary",
        "obj": "model/obj",
    }[fmt]
    tmp = tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False)
    tmp.close()
    try:
        if fmt in ("stl", "step"):
            cq.exporters.export(model, tmp.name)
        elif fmt == "3mf":
            # CadQuery doesn't export 3MF directly. Round-trip via trimesh.
            import trimesh
            stl_bytes = _export_stl(model)
            mesh = trimesh.load(io_stream(stl_bytes, ".stl"), file_type="stl")
            mesh.export(tmp.name, file_type="3mf")
        elif fmt == "glb":
            import trimesh
            stl_bytes = _export_stl(model)
            mesh = trimesh.load(io_stream(stl_bytes, ".stl"), file_type="stl")
            mesh.export(tmp.name, file_type="glb")
        elif fmt == "obj":
            import trimesh
            stl_bytes = _export_stl(model)
            mesh = trimesh.load(io_stream(stl_bytes, ".stl"), file_type="stl")
            mesh.export(tmp.name, file_type="obj")
        data = Path(tmp.name).read_bytes()
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="part.{fmt}"'})


class ConverseRequest(BaseModel):
    """Multi-turn 'describe a thing' conversation.

    Frontend tracks `history` as a list of {role, content} turns. Backend
    decides the next response: either ASK a clarifying question, or PROPOSE
    a list of ops the frontend should accept/reject.
    """
    history: List[dict]
    canvas_width: float = 600.0
    canvas_height: float = 600.0
    target_size_mm: float = 60.0


class ConverseResponse(BaseModel):
    role: Literal["assistant"] = "assistant"
    kind: Literal["question", "proposal", "error"]
    text: str
    operations: List[Op] = []
    template: Optional[str] = None
    used_llm: bool = False


@app.post("/converse", response_model=ConverseResponse)
def converse(req: ConverseRequest) -> ConverseResponse:
    """Translate a natural-language CAD request into ops via templates +/− LLM.

    Strategy:
      1. Inspect the latest user turn for a recognized noun (chair, table,
         shelf, box, cylinder, mug). If found, build directly from a template.
      2. Otherwise, if an LLM is available, ask Claude to either clarify or
         propose ops (tool-use style). Without credits this falls through.
      3. Otherwise return an "error" kind with a helpful message.
    """
    if not req.history:
        return ConverseResponse(kind="error", text="No conversation history.")
    last_user = next((m for m in reversed(req.history) if m.get("role") == "user"), None)
    if not last_user:
        return ConverseResponse(kind="error", text="No user message yet.")
    # Concatenate ALL user messages so dimensions given in a follow-up still
    # match against the noun from the first ask. The last_user dominates for
    # number extraction in templates that prefer recent values.
    combined = " ".join(m["content"] for m in req.history if m.get("role") == "user")
    last_text = last_user["content"].lower()

    # ---- Template path: recognize a noun (from combined) + extract dimensions
    template_match = _template_for(combined)
    if template_match is not None:
        name, ops, question = template_match
        if question:
            return ConverseResponse(kind="question", text=question, template=name)
        return ConverseResponse(
            kind="proposal",
            text=f"Proposed a {name} ({len(ops)} ops). Accept to add to the timeline.",
            operations=ops, template=name,
        )

    # ---- LLM fallback (only if credits available)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _llm_converse(req.history)
        except Exception:
            pass

    # ---- Fallback: hint at the user
    return ConverseResponse(
        kind="error",
        text=("I don't recognize that object yet. Templates I know: chair, table, "
              "shelf, box, cylinder, mug, knob, washer, plate, vase. "
              "You can also describe ops directly: 'fillet 2mm', 'add a 6mm hole', etc."),
    )


# ============================================================================
# Templates — generative "make me a {chair, table, box, …}"
# ============================================================================

def _template_for(text: str):
    """Returns (name, ops, question?) or None.

    If the user is asking for a known object but hasn't given a dimension,
    return (name, [], question) so the frontend can prompt them.
    """
    t = text.lower()

    # Extract any numbers from the text. Common pattern "50x30x20" or "50 by 30".
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]

    def has_word(*words):
        return any(re.search(rf"\b{w}\b", t) for w in words)

    if has_word("box", "block", "cube"):
        # Defaults if no dims.
        if not nums:
            return ("box", [], "What size? e.g. '50x30x20 mm' or 'a 40mm cube'")
        w = nums[0]; d = nums[1] if len(nums) > 1 else w; h = nums[2] if len(nums) > 2 else w
        return ("box", _box(w, d, h), None)

    if has_word("cylinder", "rod", "pin", "shaft"):
        if not nums:
            return ("cylinder", [], "Cylinder diameter and height? e.g. '20mm x 50mm'")
        dia = nums[0]; h = nums[1] if len(nums) > 1 else dia * 2
        return ("cylinder", _cylinder(dia, h), None)

    if has_word("washer"):
        if not nums:
            return ("washer", [], "Outer diameter, inner diameter, thickness? e.g. '20mm OD, 8mm ID, 3mm thick'")
        od = nums[0]; idia = nums[1] if len(nums) > 1 else od * 0.4; th = nums[2] if len(nums) > 2 else 3.0
        return ("washer", _washer(od, idia, th), None)

    if has_word("plate", "panel"):
        if len(nums) < 2:
            return ("plate", [], "Plate dimensions? e.g. '100x60x3mm with 4 holes'")
        w = nums[0]; d = nums[1]; th = nums[2] if len(nums) > 2 else 3.0
        n_holes = 4 if "hole" in t else 0
        return ("plate", _plate(w, d, th, n_holes), None)

    if has_word("chair"):
        if not nums:
            return ("chair", [], "Chair dimensions — seat width/depth/height (e.g. '40x40 seat, 45cm high, 80cm tall total'). Or just say 'default'.")
        # Pick the first few numbers; fall back to defaults.
        seat_w = nums[0] if len(nums) >= 1 else 40
        seat_d = nums[1] if len(nums) >= 2 else 40
        seat_h = nums[2] if len(nums) >= 3 else 45
        back_h = nums[3] if len(nums) >= 4 else 45
        return ("chair", _chair(seat_w, seat_d, seat_h, back_h), None)

    if has_word("table", "desk"):
        if not nums:
            return ("table", [], "Table dimensions — top width, depth, height? e.g. '120 60 75 cm' (sizes in mm here)")
        w = nums[0] if len(nums) >= 1 else 120
        d = nums[1] if len(nums) >= 2 else 60
        h = nums[2] if len(nums) >= 3 else 75
        return ("table", _table(w, d, h), None)

    if has_word("shelf", "bracket"):
        if not nums:
            return ("shelf", [], "Shelf dimensions — width, depth, support height? e.g. '200 60 40 mm'")
        w = nums[0]; d = nums[1] if len(nums) > 1 else w * 0.3; h = nums[2] if len(nums) > 2 else d * 0.7
        return ("shelf", _shelf(w, d, h), None)

    if has_word("knob"):
        if not nums:
            return ("knob", [], "Knob diameter and height? e.g. '30mm x 20mm'")
        dia = nums[0]; h = nums[1] if len(nums) > 1 else dia * 0.7
        return ("knob", _knob(dia, h), None)

    if has_word("mug", "cup"):
        if not nums:
            return ("mug", [], "Mug diameter and height? e.g. '80mm x 100mm' (handle added automatically)")
        dia = nums[0]; h = nums[1] if len(nums) > 1 else dia * 1.3
        return ("mug", _mug(dia, h), None)

    if has_word("vase"):
        if not nums:
            return ("vase", [], "Vase base diameter, neck diameter, height? e.g. '80 50 150 mm'")
        bd = nums[0]; nd = nums[1] if len(nums) > 1 else bd * 0.6; h = nums[2] if len(nums) > 2 else bd * 2
        return ("vase", _vase(bd, nd, h), None)

    return None


def _box(w: float, d: float, h: float) -> list[dict]:
    """A simple cube/box. Sketched as a rectangle stroke + extrude."""
    # Build the stroke as a closed rectangle on canvas (we put it in a 600x600 frame).
    cx, cy = 300, 300
    pts = [
        [cx - w * 2, cy - d * 2], [cx + w * 2, cy - d * 2],
        [cx + w * 2, cy + d * 2], [cx - w * 2, cy + d * 2],
        [cx - w * 2, cy - d * 2],
    ]
    return [{
        "op": "sketch_extrude",
        "strokes": [{"points": pts, "annotation": {"kind": "width", "value_mm": w}, "construction": False}],
        "height_mm": h, "plane": "XY", "mode": "new_body",
    }]


def _cylinder(dia: float, height: float) -> list[dict]:
    """A circle stroke (32-segment polygon) + extrude."""
    import math as _m
    cx, cy = 300, 300; r = dia * 2
    pts = [[cx + r * _m.cos(2 * _m.pi * i / 32), cy + r * _m.sin(2 * _m.pi * i / 32)]
           for i in range(33)]
    return [{
        "op": "sketch_extrude",
        "strokes": [{"points": pts, "annotation": {"kind": "diameter", "value_mm": dia}, "construction": False}],
        "height_mm": height, "plane": "XY", "mode": "new_body",
    }]


def _washer(od: float, idia: float, thick: float) -> list[dict]:
    """Outer ring (large circle) + inner hole (smaller circle) + extrude."""
    import math as _m
    cx, cy = 300, 300
    def circle_pts(r_canvas):
        return [[cx + r_canvas * _m.cos(2 * _m.pi * i / 32), cy + r_canvas * _m.sin(2 * _m.pi * i / 32)]
                for i in range(33)]
    return [{
        "op": "sketch_extrude",
        "strokes": [
            {"points": circle_pts(od * 2), "annotation": {"kind": "diameter", "value_mm": od}, "construction": False},
            {"points": circle_pts(idia * 2), "construction": False},
        ],
        "height_mm": thick, "plane": "XY", "mode": "new_body",
    }]


def _plate(w: float, d: float, th: float, n_holes: int) -> list[dict]:
    """A rect plate with optional 4-corner mounting holes."""
    cx, cy = 300, 300
    plate_pts = [
        [cx - w * 2, cy - d * 2], [cx + w * 2, cy - d * 2],
        [cx + w * 2, cy + d * 2], [cx - w * 2, cy + d * 2],
        [cx - w * 2, cy - d * 2],
    ]
    ops: list[dict] = [{
        "op": "sketch_extrude",
        "strokes": [{"points": plate_pts, "annotation": {"kind": "width", "value_mm": w}, "construction": False}],
        "height_mm": th, "plane": "XY", "mode": "new_body",
    }]
    if n_holes == 4:
        inset = min(w, d) * 0.12
        for sx in (-1, 1):
            for sy in (-1, 1):
                ops.append({
                    "op": "hole",
                    "x_mm": sx * (w / 2 - inset), "y_mm": sy * (d / 2 - inset),
                    "diameter_mm": 4.5, "depth_mm": None, "plane": "top",
                })
    return ops


def _chair(seat_w: float, seat_d: float, seat_h: float, back_h: float) -> list[dict]:
    """A toy chair: seat plate + 4 legs (cylinders unioned beneath) + backrest plate."""
    import math as _m
    seat_th = 4.0
    leg_dia = 4.0
    back_th = 3.0
    cx, cy = 300, 300

    # Seat as the outer extrude.
    seat_pts = [
        [cx - seat_w * 2, cy - seat_d * 2], [cx + seat_w * 2, cy - seat_d * 2],
        [cx + seat_w * 2, cy + seat_d * 2], [cx - seat_w * 2, cy + seat_d * 2],
        [cx - seat_w * 2, cy - seat_d * 2],
    ]
    ops: list[dict] = [{
        "op": "sketch_extrude",
        "strokes": [{"points": seat_pts, "annotation": {"kind": "width", "value_mm": seat_w}, "construction": False}],
        "height_mm": seat_th, "plane": "XY", "mode": "new_body",
    }]
    # 4 legs — cylinders extruded downward as separate sketch_extrudes (mode=join).
    # We add them as additive circles on the SAME sketch op for simplicity.
    leg_inset = min(seat_w, seat_d) * 0.10
    leg_strokes = []
    def circle_pts(canvas_cx, canvas_cy, r_canvas):
        return [[canvas_cx + r_canvas * _m.cos(2 * _m.pi * i / 24),
                 canvas_cy + r_canvas * _m.sin(2 * _m.pi * i / 24)] for i in range(25)]
    # Seat is positive: a tiny stroke at the centre to anchor the sketch_extrude
    # used for legs (each leg gets its own additive). We'll do it as separate
    # extrude ops with mode=join, on plane=XY, but offset Z by setting height
    # and translating via z_offset (the SketchExtrudeOp doesn't natively allow Z
    # offset, so we keep all on XY and the chair stands with seat on top of legs).
    # Practical: legs are tall cylinders, seat is a short plate on top.
    # Layout: extrude legs first (height=seat_h), then extrude the seat plate
    # again on the top of the last extrude using plane="top".
    return [
        # 1. Four legs as one sketch op with 4 additive circles.
        {
            "op": "sketch_extrude",
            "strokes": [
                {"points": circle_pts(cx - seat_w * 2 + leg_inset * 4, cy - seat_d * 2 + leg_inset * 4, leg_dia * 2), "construction": False},
                {"points": circle_pts(cx + seat_w * 2 - leg_inset * 4, cy - seat_d * 2 + leg_inset * 4, leg_dia * 2), "construction": False},
                {"points": circle_pts(cx + seat_w * 2 - leg_inset * 4, cy + seat_d * 2 - leg_inset * 4, leg_dia * 2), "construction": False},
                {"points": circle_pts(cx - seat_w * 2 + leg_inset * 4, cy + seat_d * 2 - leg_inset * 4, leg_dia * 2), "construction": False},
            ],
            "height_mm": seat_h, "plane": "XY", "mode": "new_body",
        },
        # 2. Seat plate on top of the legs.
        {
            "op": "sketch_extrude",
            "strokes": [{"points": seat_pts, "construction": False}],
            "height_mm": seat_th, "plane": "top", "mode": "join",
        },
        # 3. Backrest — a tall thin plate at the back edge.
        {
            "op": "sketch_extrude",
            "strokes": [{"points": [
                [cx - seat_w * 2, cy + seat_d * 2 - back_th * 2], [cx + seat_w * 2, cy + seat_d * 2 - back_th * 2],
                [cx + seat_w * 2, cy + seat_d * 2], [cx - seat_w * 2, cy + seat_d * 2],
                [cx - seat_w * 2, cy + seat_d * 2 - back_th * 2],
            ], "construction": False}],
            "height_mm": back_h, "plane": "top", "mode": "join",
        },
    ]


def _table(w: float, d: float, h: float) -> list[dict]:
    """Toy table: tabletop + 4 legs (similar to chair but no backrest)."""
    import math as _m
    top_th = 3.0
    leg_dia = 4.0
    cx, cy = 300, 300

    top_pts = [
        [cx - w * 2, cy - d * 2], [cx + w * 2, cy - d * 2],
        [cx + w * 2, cy + d * 2], [cx - w * 2, cy + d * 2],
        [cx - w * 2, cy - d * 2],
    ]
    leg_inset = min(w, d) * 0.08
    def circle_pts(ccx, ccy, r):
        return [[ccx + r * _m.cos(2 * _m.pi * i / 24), ccy + r * _m.sin(2 * _m.pi * i / 24)] for i in range(25)]
    return [
        {  # legs
            "op": "sketch_extrude",
            "strokes": [
                {"points": circle_pts(cx - w * 2 + leg_inset * 4, cy - d * 2 + leg_inset * 4, leg_dia * 2), "construction": False},
                {"points": circle_pts(cx + w * 2 - leg_inset * 4, cy - d * 2 + leg_inset * 4, leg_dia * 2), "construction": False},
                {"points": circle_pts(cx + w * 2 - leg_inset * 4, cy + d * 2 - leg_inset * 4, leg_dia * 2), "construction": False},
                {"points": circle_pts(cx - w * 2 + leg_inset * 4, cy + d * 2 - leg_inset * 4, leg_dia * 2), "construction": False},
            ],
            "height_mm": h, "plane": "XY", "mode": "new_body",
        },
        {  # top
            "op": "sketch_extrude",
            "strokes": [{"points": top_pts, "construction": False}],
            "height_mm": top_th, "plane": "top", "mode": "join",
        },
    ]


def _shelf(w: float, d: float, h: float) -> list[dict]:
    """L-bracket shelf: horizontal plate + vertical back."""
    cx, cy = 300, 300
    plate_pts = [
        [cx - w * 2, cy - d * 2], [cx + w * 2, cy - d * 2],
        [cx + w * 2, cy + d * 2], [cx - w * 2, cy + d * 2],
        [cx - w * 2, cy - d * 2],
    ]
    return [
        {
            "op": "sketch_extrude",
            "strokes": [{"points": plate_pts, "annotation": {"kind": "width", "value_mm": w}, "construction": False}],
            "height_mm": 3.0, "plane": "XY", "mode": "new_body",
        },
        # Back vertical plate at the back edge — done as a sketch on the top face.
        {
            "op": "sketch_extrude",
            "strokes": [{"points": [
                [cx - w * 2, cy + d * 2 - 2.0 * 2], [cx + w * 2, cy + d * 2 - 2.0 * 2],
                [cx + w * 2, cy + d * 2], [cx - w * 2, cy + d * 2],
                [cx - w * 2, cy + d * 2 - 2.0 * 2],
            ], "construction": False}],
            "height_mm": h, "plane": "top", "mode": "join",
        },
    ]


def _knob(dia: float, height: float) -> list[dict]:
    """A knob — cylinder + filleted top edges + a small thumb-grip hole."""
    cyl = _cylinder(dia, height)
    cyl.append({"op": "fillet", "radius_mm": min(dia / 6, height / 4), "target": "top"})
    cyl.append({"op": "fillet", "radius_mm": min(dia / 12, height / 8), "target": "bottom"})
    return cyl


def _mug(dia: float, height: float) -> list[dict]:
    """A mug — cylinder + shell to hollow."""
    cyl = _cylinder(dia, height)
    wall = max(2.0, dia * 0.04)
    cyl.append({"op": "shell", "thickness_mm": wall, "remove": "top"})
    return cyl


def _vase(base_dia: float, neck_dia: float, height: float) -> list[dict]:
    """A vase via revolve. The profile is a polyline tracing the side of the vase."""
    cx, cy = 300, 300
    # Profile is roughly: base at bottom, narrows toward neck, then widens slightly.
    bottom_y = 400; top_y = 400 - height * 2  # canvas Y is inverted
    half_base = base_dia * 2 / 2
    half_neck = neck_dia * 2 / 2
    half_belly = max(half_base, half_neck) * 1.15
    # Profile points (canvas coords) — outline of one side of the vase + axis return.
    profile = [
        [cx, bottom_y],
        [cx + half_base, bottom_y],
        [cx + half_belly, bottom_y - height * 0.7],
        [cx + half_neck, top_y + height * 0.3],
        [cx + half_neck * 1.08, top_y],
        [cx, top_y],
        [cx, bottom_y],
    ]
    return [{
        "op": "revolve",
        "strokes": [{"points": profile, "construction": False}],
        "angle_deg": 360.0, "axis": "Y_canvas",
    }]


def _llm_converse(history: list[dict]) -> ConverseResponse:
    """LLM-driven multi-turn converse — used when API key has credits.

    Asks Claude to either ask a clarifying question or propose ops.
    """
    import anthropic
    client = anthropic.Anthropic()
    schema = _ops_schema_for_prompt()
    system = (
        "You are a CAD design assistant. The user wants to build a 3D model. "
        "Either ask ONE clarifying question (if dimensions or geometry are still "
        "unclear) OR propose a complete list of operations using the schema below. "
        "Return ONLY JSON. Format: "
        '{"kind": "question", "text": "..."} OR '
        '{"kind": "proposal", "text": "summary", "operations": [...]}\n\n'
        f"Operations schema:\n{schema}"
    )
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048, system=system, messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.MULTILINE)
    obj = json.loads(text)
    if obj.get("kind") == "question":
        return ConverseResponse(kind="question", text=obj.get("text", ""), used_llm=True)
    return ConverseResponse(
        kind="proposal", text=obj.get("text", ""),
        operations=obj.get("operations", []), used_llm=True,
    )


@app.get("/parts", response_model=PartsListResponse)
def list_parts() -> PartsListResponse:
    """List every cad/<name>.py with a quick summary."""
    parts: list[dict] = []
    if CAD_DIR.exists():
        for p in sorted(CAD_DIR.glob("*.py")):
            if p.name.startswith("_"):
                continue
            text = p.read_text()
            # First docstring line.
            m = re.match(r'\s*"""(.*?)"""', text, re.DOTALL)
            doc = m.group(1).strip().split("\n")[0] if m else ""
            # Whether the rendered PNG exists.
            png = PROJECT_ROOT / "generated" / f"{p.stem}.png"
            parts.append({
                "name": p.stem,
                "doc": doc,
                "has_render": png.exists(),
                "size_bytes": p.stat().st_size,
            })
    return PartsListResponse(parts=parts)


@app.get("/open/{name}", response_model=OpenPartResponse)
def open_part(name: str) -> OpenPartResponse:
    """Read a cad/<name>.py file and return its parameters + docstring + source."""
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        raise HTTPException(400, "invalid name")
    src = CAD_DIR / f"{name}.py"
    if not src.exists():
        raise HTTPException(404, f"cad/{name}.py not found")
    text = src.read_text()
    m = re.match(r'\s*"""(.*?)"""', text, re.DOTALL)
    docstring = m.group(1).strip() if m else ""
    params: list[dict] = []
    for line in text.splitlines():
        m = re.match(r'^([A-Z][A-Z0-9_]*)\s*=\s*([^#\n]+?)\s*(?:#\s*(.*))?$', line)
        if m:
            params.append({
                "name": m.group(1),
                "value": m.group(2).strip(),
                "comment": (m.group(3) or "").strip(),
            })
        elif re.match(r'^(def |class |result\s*=|render\s*\()', line.lstrip()):
            break
    return OpenPartResponse(name=name, docstring=docstring, parameters=params, source=text)


# ---- helpers ---------------------------------------------------------------

def io_stream(data: bytes, suffix: str):
    """Wrap bytes in a file-like object suitable for trimesh.load."""
    import io
    bio = io.BytesIO(data)
    bio.name = f"buf{suffix}"
    return bio


def _llm_parse(unparsed_lines: list[str]) -> tuple[list[Op], list[str]]:
    """Call Claude to translate freeform CAD instructions into ops.

    Returns (parsed_ops, still_unparsed). Failure modes (no key, network error,
    malformed JSON) all degrade gracefully — the unparsed lines are returned
    unchanged so the frontend can show them to the user.
    """
    try:
        import anthropic
    except ImportError:
        return [], unparsed_lines

    client = anthropic.Anthropic()
    schema = _ops_schema_for_prompt()
    user_text = "\n".join(f"- {line}" for line in unparsed_lines)

    system = (
        "You are a CAD instruction parser. Translate plain-English CAD edit "
        "instructions into a JSON array of operations against the schema below. "
        "Return ONLY a JSON array — no prose, no markdown fences. "
        "If a line cannot be translated, omit it.\n\n"
        f"Schema:\n{schema}\n\n"
        "Conventions:\n"
        "- All lengths in mm. Default values: fillet radius 2mm, chamfer 1mm, "
        "hole diameter 5mm, height 10mm.\n"
        "- 'fillet the corners' → fillet target='vertical'.\n"
        "- 'round the top' → fillet target='top'.\n"
        "- 'mirror it' → mirror across YZ.\n"
        "- Coordinates default to (0, 0) when 'at center' or unspecified."
    )

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        # Strip any fences just in case.
        text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.MULTILINE)
        ops_raw = json.loads(text)
        if not isinstance(ops_raw, list):
            return [], unparsed_lines
        parsed: list[Op] = []
        for raw in ops_raw:
            try:
                # Use the discriminated-union machinery: each item must have an "op" key.
                # We validate via the Op annotation through a small adapter request.
                op_type = raw.get("op")
                cls = {
                    "fillet": FilletOp, "chamfer": ChamferOp, "hole": HoleOp,
                    "set_height": SetHeightOp, "mirror": MirrorOp,
                    "pattern_linear": PatternLinearOp, "circular_pattern": CircularPatternOp,
                    "shell": ShellOp,
                }.get(op_type)
                if cls is None:
                    continue
                parsed.append(cls(**raw))
            except Exception:
                continue
        # Everything parsed successfully → no remaining unparsed lines.
        return parsed, [] if parsed else unparsed_lines
    except Exception:
        return [], unparsed_lines


def _ops_schema_for_prompt() -> str:
    return """[
  {"op": "fillet", "radius_mm": float, "target": "all"|"top"|"bottom"|"vertical"},
  {"op": "chamfer", "distance_mm": float, "target": "all"|"top"|"bottom"|"vertical"},
  {"op": "hole", "x_mm": float, "y_mm": float, "diameter_mm": float, "depth_mm": float|null, "plane": "XY"|"top"|"bottom"},
  {"op": "set_height", "value_mm": float},
  {"op": "mirror", "plane": "YZ"|"XZ"},
  {"op": "pattern_linear", "axis": "x"|"y", "count": int, "spacing_mm": float},
  {"op": "circular_pattern", "count": int, "radius_mm": float, "cx_mm": float, "cy_mm": float},
  {"op": "shell", "thickness_mm": float, "remove": "top"|"bottom"|"none"}
]"""


# ============================================================================
# Operation execution
# ============================================================================

def _execute_ops(
    req: BuildRequest, force_primitive: Optional[str] = None,
) -> tuple[Any, list[OpFeedback], list[StrokeInterpretation]]:
    """Walk the op list, mutate a model, return (model, feedback, interpretations)."""
    feedback: list[OpFeedback] = []
    interps: list[StrokeInterpretation] = []
    model = None  # the cq.Workplane / cq.Shape being built up
    last_extrude_height: Optional[float] = None
    last_hole_centre: Optional[Tuple[float, float]] = None

    def to_cad_factory(req_: BuildRequest, strokes: list[Stroke]):
        """Return (transform, used_annotation_scale_bool). Maps canvas → CAD coords."""
        all_pts = [p for s in strokes for p in s.points]
        if not all_pts:
            return None, False
        xs = [p[0] for p in all_pts]; ys = [p[1] for p in all_pts]
        cx_canvas = (max(xs) + min(xs)) / 2
        cy_canvas = (max(ys) + min(ys)) / 2
        extent = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0

        # If any stroke has an annotation, scale so the first annotated stroke
        # matches its stated dimension. Otherwise scale to target_size_mm.
        scale = req_.target_size_mm / extent
        used_annotation = False
        for s in strokes:
            if s.annotation is None:
                continue
            # Find the stroke's own pixel extent (depends on annotation kind).
            sxs = [p[0] for p in s.points]; sys_ = [p[1] for p in s.points]
            if s.annotation.kind in ("diameter", "size"):
                stroke_extent = max(max(sxs) - min(sxs), max(sys_) - min(sys_)) or 1.0
                scale = s.annotation.value_mm / stroke_extent
            elif s.annotation.kind == "width":
                w = (max(sxs) - min(sxs)) or 1.0
                scale = s.annotation.value_mm / w
            elif s.annotation.kind == "height":
                h = (max(sys_) - min(sys_)) or 1.0
                scale = s.annotation.value_mm / h
            used_annotation = True
            break

        def f(p):
            return ((p[0] - cx_canvas) * scale, -(p[1] - cy_canvas) * scale)
        return f, used_annotation

    for i, op in enumerate(req.operations):
        try:
            if isinstance(op, SketchExtrudeOp):
                # Filter out construction strokes — they don't extrude.
                geom_strokes = [s for s in op.strokes if not s.construction]
                to_cad, _ = to_cad_factory(req, geom_strokes)
                if to_cad is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no non-construction strokes"))
                    continue
                # Per-stroke classify, then compose by extruding outer + booleaning
                # additives and holes. (CadQuery doesn't expose 2D face booleans
                # on a Workplane, so we do the composition at the 3D level.)
                valid: list[tuple[int, list[Point], tuple[str, dict]]] = []
                for orig_idx, s in enumerate(op.strokes):
                    if s.construction:
                        interps.append(StrokeInterpretation(
                            op_index=i, stroke_index=orig_idx, kind="polygon",
                            role="construction",
                            description="construction (not extruded)",
                        ))
                        continue
                    if len(s.points) < 5:
                        interps.append(StrokeInterpretation(
                            op_index=i, stroke_index=orig_idx, kind="polygon",
                            role="skipped",
                            description=f"too few points ({len(s.points)})",
                        ))
                        continue
                    stroke_cad = [to_cad(p) for p in s.points]
                    cls = _classify(stroke_cad, force=force_primitive)
                    valid.append((orig_idx, stroke_cad, cls))
                if not valid:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no usable strokes"))
                    continue
                outer_local = max(range(len(valid)),
                                   key=lambda k: (abs(_shoelace_area(valid[k][1])),
                                                  _bbox_area(valid[k][1])))
                outer_pts = valid[outer_local][1]
                outer_kind, outer_params = valid[outer_local][2]
                extrusion = _make_face(outer_kind, outer_params).extrude(op.height_mm)
                for li, (orig_idx, stroke, (kind, params)) in enumerate(valid):
                    if li == outer_local:
                        role = "outer"
                    else:
                        sample = stroke[:: max(1, len(stroke) // 12)] or stroke
                        inside = sum(_point_inside(p, outer_pts) for p in sample)
                        if inside >= 0.8 * len(sample):
                            role = "hole"
                            overshoot = max(2.0, op.height_mm * 0.3)
                            hole = (_make_face(kind, params)
                                    .extrude(op.height_mm + 2 * overshoot)
                                    .translate((0, 0, -overshoot)))
                            extrusion = extrusion.cut(hole)
                        else:
                            role = "additive"
                            add = _make_face(kind, params).extrude(op.height_mm)
                            extrusion = extrusion.union(add)
                    interps.append(StrokeInterpretation(
                        op_index=i, stroke_index=orig_idx, kind=kind, role=role,
                        description=_describe(kind, params, role),
                    ))
                z_offset = _plane_z_offset(model, op.plane)
                if z_offset != 0:
                    extrusion = extrusion.translate((0, 0, z_offset))
                # Boolean compose per the Fusion-style mode.
                if model is None or op.mode == "new_body":
                    model = extrusion
                elif op.mode == "join":
                    model = model.union(extrusion)
                elif op.mode == "cut":
                    model = model.cut(extrusion)
                elif op.mode == "intersect":
                    model = model.intersect(extrusion)
                last_extrude_height = op.height_mm
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"extruded {len(valid)} feature(s) by {op.height_mm:.1f}mm · mode={op.mode}"))

            elif isinstance(op, SketchCutOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot cut from empty model"))
                    continue
                to_cad, _ = to_cad_factory(req, op.strokes)
                face, _ = _build_face_from_strokes(op.strokes, to_cad, force_primitive)
                if face is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no usable face"))
                    continue
                z_offset = _plane_z_offset(model, op.plane)
                depth = op.depth_mm if op.depth_mm is not None else 1000.0
                cutter = face.extrude(depth).translate((0, 0, z_offset - depth + 1))
                model = model.cut(cutter)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"cut {len(op.strokes)} stroke(s) by {depth:.1f}mm"))

            elif isinstance(op, HoleOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot drill into empty model"))
                    continue
                bb = model.val().BoundingBox()
                z_top = bb.zmax + 1.0
                z_bot = bb.zmin - 1.0
                # Depth: through if None, otherwise depth_mm into the part from the chosen plane.
                if op.depth_mm is None:
                    depth = (z_top - z_bot)
                    start_z = z_bot
                else:
                    depth = op.depth_mm + 2
                    z_face = z_top if op.plane in ("XY", "top") else z_bot
                    start_z = z_face - 1 if op.plane in ("XY", "top") else z_face - op.depth_mm - 1
                drill = (cq.Workplane("XY")
                         .center(op.x_mm, op.y_mm)
                         .circle(op.diameter_mm / 2)
                         .extrude(depth)
                         .translate((0, 0, start_z)))
                model = model.cut(drill)
                last_hole_centre = (op.x_mm, op.y_mm)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"hole ⌀{op.diameter_mm:.1f}mm at ({op.x_mm:.1f},{op.y_mm:.1f})"))

            elif isinstance(op, FilletOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot fillet empty model"))
                    continue
                model = _apply_fillet_or_chamfer(model, op.target, "fillet", op.radius_mm)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"fillet {op.target} edges {op.radius_mm:.1f}mm"))

            elif isinstance(op, ChamferOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot chamfer empty model"))
                    continue
                model = _apply_fillet_or_chamfer(model, op.target, "chamfer", op.distance_mm)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"chamfer {op.target} edges {op.distance_mm:.1f}mm"))

            elif isinstance(op, SetHeightOp):
                # Find the LAST sketch_extrude op and rebuild the chain with that
                # height — for simplicity we just patch the request in place and
                # re-execute from scratch. Since this is run inside the loop, we
                # short-circuit and re-execute the chain.
                # ... but that's recursive. For now, just record it as feedback
                # and tell the user to re-issue the build with the new height.
                if last_extrude_height is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no extrude to modify"))
                else:
                    # Scale-translate the whole model from old height to new height
                    # in Z only. (Easy and approximate; for parts with non-trivial
                    # Z structure this would distort, but for single-extrude parts
                    # it's exactly right.)
                    if last_extrude_height > 0:
                        sf = op.value_mm / last_extrude_height
                        # Hard to do general anisotropic scale in CadQuery; instead
                        # punt and re-execute by mutating the relevant prior op.
                        # We do this by mutating req.operations and restarting.
                        for prev_idx in range(i - 1, -1, -1):
                            if isinstance(req.operations[prev_idx], SketchExtrudeOp):
                                req.operations[prev_idx].height_mm = op.value_mm
                                break
                        # Re-execute the chain from the start.
                        return _execute_ops(req, force_primitive)
                    feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                                summary=f"height set to {op.value_mm:.1f}mm"))

            elif isinstance(op, PatternLinearOp):
                # Pattern the LAST hole or sketch_cut op. Find it.
                pattern_target = None
                for prev_idx in range(i - 1, -1, -1):
                    if isinstance(req.operations[prev_idx], HoleOp):
                        pattern_target = req.operations[prev_idx]
                        break
                if pattern_target is None or model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no hole to pattern"))
                    continue
                dx = op.spacing_mm if op.axis == "x" else 0
                dy = op.spacing_mm if op.axis == "y" else 0
                for k in range(1, op.count):
                    drill = (cq.Workplane("XY")
                             .center(pattern_target.x_mm + dx * k,
                                     pattern_target.y_mm + dy * k)
                             .circle(pattern_target.diameter_mm / 2)
                             .extrude(1000)
                             .translate((0, 0, -500)))
                    model = model.cut(drill)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"pattern {op.count} along {op.axis} every {op.spacing_mm:.1f}mm"))

            elif isinstance(op, MirrorOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot mirror empty model"))
                    continue
                mirror_plane = "YZ" if op.plane == "YZ" else "XZ"
                mirrored = model.mirror(mirror_plane)
                model = model.union(mirrored)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"mirror across {op.plane}"))

            elif isinstance(op, RevolveOp):
                # Build a profile face from the largest non-construction stroke,
                # then revolve around the chosen axis. Auto-shift the profile so
                # it sits entirely on one side of the axis (CadQuery's revolve
                # requires this — profiles crossing the axis fail with BRep_API).
                geom_strokes = [s for s in op.strokes if not s.construction]
                to_cad, _ = to_cad_factory(req, geom_strokes)
                if to_cad is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no profile strokes"))
                    continue
                profiles = []
                for s in geom_strokes:
                    if len(s.points) < 5:
                        continue
                    pts = [to_cad(p) for p in s.points]
                    profiles.append((pts, _classify(pts)))
                if not profiles:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no usable profile"))
                    continue
                profiles.sort(key=lambda pc: abs(_shoelace_area(pc[0])), reverse=True)
                profile_pts, (pkind, pparams) = profiles[0]
                # Figure out the shift required to push the profile entirely off
                # the axis. For Y-axis revolve: shift X so min_x >= 0 (with a
                # small clearance). For X-axis revolve: shift Y so min_y >= 0.
                xs = [p[0] for p in profile_pts]; ys = [p[1] for p in profile_pts]
                if op.axis in ("Y", "Y_canvas"):
                    shift_x = max(0.0, -min(xs)) + 0.1
                    shift_y = 0.0
                else:
                    shift_x = 0.0
                    shift_y = max(0.0, -min(ys)) + 0.1
                # Re-build the face with the shift applied to the params.
                if pkind == "circle":
                    pparams = dict(pparams)
                    pparams["cx"] += shift_x; pparams["cy"] += shift_y
                elif pkind == "rect":
                    pparams = dict(pparams)
                    pparams["cx"] += shift_x; pparams["cy"] += shift_y
                else:
                    pparams = {"points": [(x + shift_x, y + shift_y) for x, y in pparams["points"]]}
                profile = _make_face(pkind, pparams)
                if op.axis in ("Y", "Y_canvas"):
                    axis_start, axis_end = (0, -1000, 0), (0, 1000, 0)
                else:
                    axis_start, axis_end = (-1000, 0, 0), (1000, 0, 0)
                try:
                    solid = profile.revolve(op.angle_deg, axis_start, axis_end)
                except Exception as e:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary=f"revolve failed: {type(e).__name__}: {e}"))
                    continue
                if model is None:
                    model = solid
                else:
                    model = model.union(solid)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"revolve {op.angle_deg:.0f}° around {op.axis}"))

            elif isinstance(op, ShellOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot shell empty model"))
                    continue
                # CadQuery: .faces(selector).shell(thickness). Selecting the top
                # face by default; the user can change with `remove`.
                sel = {"top": ">Z", "bottom": "<Z", "none": None}[op.remove]
                try:
                    if sel is None:
                        # Shell with no face removed isn't really meaningful in CadQuery
                        # — treat as "thicken inward" which CQ doesn't directly support.
                        # Fall back to removing the top face anyway.
                        sel = ">Z"
                    model = model.faces(sel).shell(-op.thickness_mm)
                    feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                                summary=f"shell {op.thickness_mm:.1f}mm, removed {op.remove} face"))
                except Exception as e:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary=f"shell failed: {type(e).__name__}: {e}"))

            elif isinstance(op, CircularPatternOp):
                if model is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="cannot pattern empty model"))
                    continue
                # Find the most recent hole op to replicate.
                pattern_target = None
                for prev_idx in range(i - 1, -1, -1):
                    if isinstance(req.operations[prev_idx], HoleOp):
                        pattern_target = req.operations[prev_idx]
                        break
                if pattern_target is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no prior hole op to pattern"))
                    continue
                # Place `count` copies on a circle of given radius around (cx, cy).
                import math as _m
                for k in range(op.count):
                    angle = 2 * _m.pi * k / op.count
                    px = op.cx_mm + op.radius_mm * _m.cos(angle)
                    py = op.cy_mm + op.radius_mm * _m.sin(angle)
                    drill = (cq.Workplane("XY")
                             .center(px, py)
                             .circle(pattern_target.diameter_mm / 2)
                             .extrude(1000)
                             .translate((0, 0, -500)))
                    model = model.cut(drill)
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"circular pattern {op.count}× r={op.radius_mm}mm"))

            else:
                feedback.append(OpFeedback(index=i, op=getattr(op, "op", "?"),
                                            status="skipped",
                                            summary=f"unsupported op type"))
        except HTTPException:
            raise
        except Exception as e:
            feedback.append(OpFeedback(index=i, op=getattr(op, "op", "?"),
                                        status="error",
                                        summary=f"{type(e).__name__}: {e}"))

    return model, feedback, interps


def _plane_z_offset(model, plane: str) -> float:
    if model is None or plane == "XY":
        return 0.0
    bb = model.val().BoundingBox()
    if plane == "top":
        return bb.zmax
    if plane == "bottom":
        return bb.zmin
    return 0.0


def _apply_fillet_or_chamfer(model, target: str, kind: str, value: float):
    selectors = {
        "all": None,
        "top": "%PLANE and >Z",  # not a standard CQ selector, see below
        "bottom": "%PLANE and <Z",
        "vertical": "|Z",
    }
    fn = "fillet" if kind == "fillet" else "chamfer"
    if target == "all":
        return getattr(model.edges(), fn)(value)
    if target == "vertical":
        return getattr(model.edges("|Z"), fn)(value)
    # Top/bottom edges = edges that lie on the topmost/bottommost face. Use a
    # simple selector: edges parallel to X or Y at the extreme Z. That misses
    # diagonal edges but covers axis-aligned extrusions which is the common case.
    bb = model.val().BoundingBox()
    if target == "top":
        return getattr(model.edges(">Z"), fn)(value)
    if target == "bottom":
        return getattr(model.edges("<Z"), fn)(value)
    return model


# ---- script emission (carry over from old version, adapted) -----------------

def _export_stl(model) -> bytes:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        cq.exporters.export(model, str(tmp_path))
        return tmp_path.read_bytes()
    except Exception as e:
        raise HTTPException(500, f"STL export error: {type(e).__name__}: {e}")
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _emit_script(req: BuildRequest, name: str) -> str:
    """Emit a runnable cad/<name>.py from the op list.

    Handles multi-sketch compositions (modes new_body/join/cut/intersect) and
    plane=top/bottom by tracking a running `Z_TOP` / `Z_BOT` in the emitted
    code that updates after each extrude.
    """
    lines: list[str] = [
        f'"""Generated by the sketch-to-CAD drawing app.',
        '',
        f'Reconstructs the model that was on screen at save time. Tweak the parameters',
        f'at the top and re-run.',
        '"""',
        'import sys; sys.path.insert(0, "cad")',
        'from _render import render',
        'import cadquery as cq',
        '',
    ]
    body: list[str] = []
    feature_counter = 0
    result_initialized = False
    has_sketch = any(isinstance(o, SketchExtrudeOp) for o in req.operations)
    # Track running Z extents as Python variables in the emitted script.
    body.append("Z_TOP = 0.0   # top Z of the current model (mm)")
    body.append("Z_BOT = 0.0   # bottom Z of the current model (mm)")

    for op_idx, op in enumerate(req.operations):
        if isinstance(op, SketchExtrudeOp):
            to_cad, _ = _to_cad_for_strokes(req, op.strokes)
            if to_cad is None:
                body.append(f'# Op {op_idx}: sketch_extrude — skipped (no strokes)')
                continue
            classified: list[tuple[int, list[Point], tuple[str, dict], str]] = []
            valid_strokes: list[tuple[list[Point], tuple[str, dict]]] = []
            for orig_idx, s in enumerate(op.strokes):
                if s.construction or len(s.points) < 5:
                    continue
                stroke_cad = [to_cad(p) for p in s.points]
                cls = _classify(stroke_cad)
                valid_strokes.append((stroke_cad, cls))
            if not valid_strokes:
                body.append(f'# Op {op_idx}: sketch_extrude — skipped (no usable strokes)')
                continue
            outer_local = max(range(len(valid_strokes)),
                               key=lambda k: (abs(_shoelace_area(valid_strokes[k][0])),
                                              _bbox_area(valid_strokes[k][0])))
            outer_pts = valid_strokes[outer_local][0]
            for li, (stroke, cls) in enumerate(valid_strokes):
                if li == outer_local:
                    role = "outer"
                else:
                    sample = stroke[:: max(1, len(stroke) // 12)] or stroke
                    inside = sum(_point_inside(p, outer_pts) for p in sample)
                    role = "hole" if inside >= 0.8 * len(sample) else "additive"
                classified.append((li, stroke, cls, role))

            lines.append(f'# ---- Op {op_idx}: extrude {len(valid_strokes)} stroke(s) by {op.height_mm}mm '
                         f'on plane={op.plane} mode={op.mode} ----')
            lines.append(f'H_OP_{op_idx} = {op.height_mm:.3f}')
            lines.append('')
            # Build the extrusion as a SEPARATE variable so we can boolean-compose.
            body.append(f'# Op {op_idx}: sketch_extrude (mode={op.mode}, plane={op.plane})')
            face_exprs: list[tuple[str, str]] = []  # (role, expr)
            for li, stroke, (kind, params), role in classified:
                feature_counter += 1
                prefix = f"F{feature_counter}"
                if kind == "circle":
                    lines.append(f'# Feature {feature_counter} ({role}) — circle')
                    lines.append(f'{prefix}_CX = {params["cx"]:.3f}')
                    lines.append(f'{prefix}_CY = {params["cy"]:.3f}')
                    lines.append(f'{prefix}_R  = {params["r"]:.3f}')
                    face_expr = f'cq.Workplane("XY").center({prefix}_CX, {prefix}_CY).circle({prefix}_R)'
                elif kind == "rect":
                    lines.append(f'# Feature {feature_counter} ({role}) — rect')
                    lines.append(f'{prefix}_CX = {params["cx"]:.3f}')
                    lines.append(f'{prefix}_CY = {params["cy"]:.3f}')
                    lines.append(f'{prefix}_W  = {params["w"]:.3f}')
                    lines.append(f'{prefix}_H  = {params["h"]:.3f}')
                    face_expr = f'cq.Workplane("XY").center({prefix}_CX, {prefix}_CY).rect({prefix}_W, {prefix}_H)'
                else:
                    pts = params["points"]
                    lines.append(f'# Feature {feature_counter} ({role}) — polygon ({len(pts)} pts)')
                    lines.append(f'{prefix}_PTS = {pts!r}')
                    face_expr = f'cq.Workplane("XY").polyline({prefix}_PTS).close()'
                lines.append('')
                face_exprs.append((role, face_expr))
            # Compose this op's extrusion in a temp variable, then merge.
            outer_face = next(fe for r, fe in face_exprs if r == 'outer')
            body.append(f'_op_solid = {outer_face}.extrude(H_OP_{op_idx})')
            for r, fe in face_exprs:
                if r == 'additive':
                    body.append(f'_op_solid = _op_solid.union({fe}.extrude(H_OP_{op_idx}))')
                elif r == 'hole':
                    body.append(f'_op_solid = _op_solid.cut({fe}.extrude(H_OP_{op_idx} + 4).translate((0,0,-2)))')
            # Plane offset
            if op.plane == "top":
                body.append(f'_op_solid = _op_solid.translate((0, 0, Z_TOP))')
            elif op.plane == "bottom":
                body.append(f'_op_solid = _op_solid.translate((0, 0, Z_BOT - H_OP_{op_idx}))')
            # Boolean compose with running result.
            if not result_initialized:
                body.append(f'result = _op_solid')
                result_initialized = True
                # Initialize Z_TOP / Z_BOT.
                if op.plane == "bottom":
                    body.append(f'Z_BOT -= H_OP_{op_idx}')
                else:
                    body.append(f'Z_TOP += H_OP_{op_idx}')
            else:
                if op.mode == "new_body":
                    body.append(f'result = _op_solid')
                elif op.mode == "join":
                    body.append(f'result = result.union(_op_solid)')
                elif op.mode == "cut":
                    body.append(f'result = result.cut(_op_solid)')
                elif op.mode == "intersect":
                    body.append(f'result = result.intersect(_op_solid)')
                # Update Z extents — assume mode=join adds to the top if plane=top.
                if op.plane == "top" and op.mode in ("join", "new_body"):
                    body.append(f'Z_TOP += H_OP_{op_idx}')
                elif op.plane == "bottom" and op.mode in ("join", "new_body"):
                    body.append(f'Z_BOT -= H_OP_{op_idx}')
            body.append('')

        elif isinstance(op, HoleOp):
            depth_expr = f'1000' if op.depth_mm is None else f'{op.depth_mm + 4:.3f}'
            offset_z = -500 if op.depth_mm is None else -2
            body.append(f'# Op {op_idx}: drill ⌀{op.diameter_mm}mm hole at ({op.x_mm},{op.y_mm})')
            body.append(f'_drill = (cq.Workplane("XY")')
            body.append(f'          .center({op.x_mm:.3f}, {op.y_mm:.3f})')
            body.append(f'          .circle({op.diameter_mm/2:.3f})')
            body.append(f'          .extrude({depth_expr})')
            body.append(f'          .translate((0, 0, {offset_z})))')
            body.append(f'result = result.cut(_drill)')

        elif isinstance(op, FilletOp):
            if op.target == "all":
                body.append(f'# Op {op_idx}: fillet all edges {op.radius_mm}mm')
                body.append(f'result = result.edges().fillet({op.radius_mm:.3f})')
            elif op.target == "top":
                body.append(f'result = result.edges(">Z").fillet({op.radius_mm:.3f})')
            elif op.target == "bottom":
                body.append(f'result = result.edges("<Z").fillet({op.radius_mm:.3f})')
            elif op.target == "vertical":
                body.append(f'result = result.edges("|Z").fillet({op.radius_mm:.3f})')

        elif isinstance(op, ChamferOp):
            if op.target == "all":
                body.append(f'result = result.edges().chamfer({op.distance_mm:.3f})')
            elif op.target == "top":
                body.append(f'result = result.edges(">Z").chamfer({op.distance_mm:.3f})')
            elif op.target == "bottom":
                body.append(f'result = result.edges("<Z").chamfer({op.distance_mm:.3f})')
            elif op.target == "vertical":
                body.append(f'result = result.edges("|Z").chamfer({op.distance_mm:.3f})')

        elif isinstance(op, MirrorOp):
            body.append(f'# Op {op_idx}: mirror across {op.plane}')
            body.append(f'result = result.union(result.mirror("{op.plane}"))')

        elif isinstance(op, PatternLinearOp):
            body.append(f'# Op {op_idx}: linear pattern N={op.count} along {op.axis} every {op.spacing_mm}mm')
            body.append(f'# (saved form does not preserve the patterned op; ignored — re-add from the app)')

        elif isinstance(op, SetHeightOp):
            body.append(f'# Op {op_idx}: set_height was applied at the previous sketch_extrude — already baked into H_OP')

        else:
            body.append(f'# Op {op_idx}: {op.op} — not yet emittable in saved form')

    if not has_sketch or not result_initialized:
        lines.append('# Note: no sketch_extrude in the op list; nothing to render.')
        lines.append('result = cq.Workplane("XY").box(1, 1, 1)   # tiny placeholder')
        lines.extend(body)
    else:
        lines.append('# ---- geometry --------------------------------------------------------------')
        lines.extend(body)

    lines.append('')
    lines.append(f'render(result, "{name}")')
    return "\n".join(lines)


def _to_cad_for_strokes(req: BuildRequest, strokes: list[Stroke]):
    """Same canvas→CAD scale logic as inside _execute_ops, lifted out for _emit_script."""
    all_pts = [p for s in strokes for p in s.points]
    if not all_pts:
        return None, False
    xs = [p[0] for p in all_pts]; ys = [p[1] for p in all_pts]
    cx = (max(xs) + min(xs)) / 2
    cy = (max(ys) + min(ys)) / 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    scale = req.target_size_mm / extent
    used_annotation = False
    for s in strokes:
        if s.annotation is None:
            continue
        sxs = [p[0] for p in s.points]; sys_ = [p[1] for p in s.points]
        if s.annotation.kind in ("diameter", "size"):
            se = max(max(sxs) - min(sxs), max(sys_) - min(sys_)) or 1.0
            scale = s.annotation.value_mm / se
        elif s.annotation.kind == "width":
            se = (max(sxs) - min(sxs)) or 1.0
            scale = s.annotation.value_mm / se
        elif s.annotation.kind == "height":
            se = (max(sys_) - min(sys_)) or 1.0
            scale = s.annotation.value_mm / se
        used_annotation = True
        break
    return (lambda p: ((p[0] - cx) * scale, -(p[1] - cy) * scale)), used_annotation


# ============================================================================
# Stroke classification (per-stroke, used inside SketchExtrudeOp / SketchCutOp)
# ============================================================================

def _make_face(kind: str, params: dict):
    if kind == "circle":
        return cq.Workplane("XY").center(params["cx"], params["cy"]).circle(params["r"])
    if kind == "rect":
        return cq.Workplane("XY").center(params["cx"], params["cy"]).rect(params["w"], params["h"])
    pts = params["points"]
    if len(pts) < 3:
        raise ValueError("polygon collapsed to < 3 points")
    return cq.Workplane("XY").polyline(pts).close()


def _classify(stroke: list[Point], force: Optional[str] = None) -> tuple[str, dict]:
    if force == "circle":
        cx, cy = _centroid(stroke)
        r = sum(math.hypot(p[0] - cx, p[1] - cy) for p in stroke) / len(stroke)
        return "circle", {"cx": cx, "cy": cy, "r": r}
    if force == "rect":
        bb = _bbox(stroke)
        return "rect", {"cx": (bb[0] + bb[2]) / 2, "cy": (bb[1] + bb[3]) / 2,
                        "w": bb[2] - bb[0], "h": bb[3] - bb[1]}
    if force == "polygon":
        return "polygon", {"points": _simplify(stroke, tolerance=_extent(stroke) * 0.02)}

    cx, cy = _centroid(stroke)
    radii = [math.hypot(p[0] - cx, p[1] - cy) for p in stroke]
    mean_r = sum(radii) / len(radii)
    is_circle = False
    if mean_r > 0:
        std = math.sqrt(sum((r - mean_r) ** 2 for r in radii) / len(radii))
        is_circle = (std / mean_r < 0.18) and len(stroke) >= 10
    if is_circle:
        bb = _bbox(stroke)
        bw = bb[2] - bb[0]; bh = bb[3] - bb[1]
        if bw > 0 and bh > 0:
            ar = max(bw, bh) / min(bw, bh)
            if ar > 1.25:
                is_circle = False
            poly_area = abs(_shoelace_area(stroke))
            if bw * bh > 0 and poly_area / (bw * bh) > 0.9:
                is_circle = False
    if is_circle:
        return "circle", {"cx": cx, "cy": cy, "r": mean_r}

    bb = _bbox(stroke)
    bbox_area = (bb[2] - bb[0]) * (bb[3] - bb[1])
    poly_area = abs(_shoelace_area(stroke))
    if poly_area > 0 and bbox_area > 0 and bbox_area / poly_area < 1.18:
        return "rect", {"cx": (bb[0] + bb[2]) / 2, "cy": (bb[1] + bb[3]) / 2,
                        "w": bb[2] - bb[0], "h": bb[3] - bb[1]}

    return "polygon", {"points": _simplify(stroke, tolerance=_extent(stroke) * 0.02)}


def _describe(kind: str, params: dict, role: str) -> str:
    if kind == "circle":
        return f"circle ⌀{2 * params['r']:.1f}mm — {role}"
    if kind == "rect":
        return f"rect {params['w']:.1f}×{params['h']:.1f}mm — {role}"
    n = len(params.get("points", []))
    return f"polygon ({n} pts) — {role}"


# ============================================================================
# Text prompt parser  (English → ops)
# ============================================================================

def parse_prompt(text: str) -> tuple[list[Op], list[str]]:
    """Translate a natural-English text prompt into a list of operations.

    Each line / sentence is matched against patterns in order. Lines that don't
    match any pattern are returned in `unparsed` so the frontend can show them
    to the user.
    """
    ops: list[Op] = []
    unparsed: list[str] = []
    # Split on newlines, semicolons, and periods that are NOT inside numbers
    # (don't break "0.5mm" or "12.7mm").
    raw_parts = re.split(r'(?:\n|;|(?<!\d)\.(?!\d))', text)
    parts = [p.strip() for p in raw_parts if p.strip()]
    for part in parts:
        op = _parse_one(part)
        if op is not None:
            ops.append(op)
        else:
            unparsed.append(part)
    return ops, unparsed


_NUMBER = r"(\d+(?:\.\d+)?)"


def _parse_one(line: str) -> Optional[Op]:
    s = line.lower().strip()

    # First, extract any clear target keyword.
    target = _extract_target(s)

    # Alias normalization — smooth/round/soften → fillet semantics.
    if re.search(r"\b(smooth|smoothen|soften)\b", s) and not re.search(r"\bfillet\b", s):
        s_norm = s + " (fillet)"
        m = re.search(rf"{_NUMBER}\s*mm", s_norm)
        if m:
            return FilletOp(radius_mm=float(m.group(1)), target=target or "all")
        else:
            # Default to 2mm.
            return FilletOp(radius_mm=2.0, target=target or "all")

    # Fillet / "round the corners/edges"
    if re.search(r"\b(fillet|round)\b", s):
        m = re.search(rf"{_NUMBER}\s*mm", s)
        if m:
            r = float(m.group(1))
            t = target
            if t is None and re.search(r"\bcorner", s):
                t = "vertical"
            return FilletOp(radius_mm=r, target=t or "all")

    # Chamfer / "bevel"
    if re.search(r"\b(chamfer|bevel)\b", s):
        m = re.search(rf"{_NUMBER}\s*mm", s)
        if m:
            return ChamferOp(distance_mm=float(m.group(1)), target=target or "all")

    # Set height: "make it X mm tall/high/thick" / "height X mm" / "X mm thick"
    m = re.search(rf"(?:height\s+(?:is\s+|=\s*)?|{_NUMBER}\s*mm\s*(?:tall|high|thick|deep))", s)
    if m:
        # Need to extract the number; do a second search.
        m2 = re.search(rf"{_NUMBER}\s*mm", s)
        if m2 and re.search(r"\b(tall|high|thick|deep|height)\b", s):
            return SetHeightOp(value_mm=float(m2.group(1)))

    # Hole — accept defaults if dia not given ("drill a hole" → 5mm)
    if re.search(r"\b(hole|drill)\b", s):
        d = None
        m = re.search(rf"(?:⌀|\bdia(?:meter)?\b\s*)?{_NUMBER}\s*mm", s)
        if m:
            d = float(m.group(1))
        x, y = 0.0, 0.0
        if "top right" in s or "upper right" in s:    x, y = 15, 15
        elif "top left" in s or "upper left" in s:    x, y = -15, 15
        elif "bottom right" in s or "lower right" in s: x, y = 15, -15
        elif "bottom left" in s or "lower left" in s: x, y = -15, -15
        else:
            mm = re.search(rf"\(\s*(-?{_NUMBER})\s*,\s*(-?{_NUMBER})\s*\)", s)
            if mm: x = float(mm.group(1)); y = float(mm.group(3))
        # Default diameter if none given.
        if d is None:
            d = 5.0
        return HoleOp(x_mm=x, y_mm=y, diameter_mm=d, plane="top")

    # Mirror
    m = re.search(r"mirror(?:\s+(?:it|across))?\s*(yz|xz|x\b|y\b)?", s)
    if m and "mirror" in s:
        token = (m.group(1) or "yz").upper()
        plane = "YZ" if token in ("YZ", "X") else "XZ"
        return MirrorOp(plane=plane)

    # Pattern linear
    m = re.search(rf"(?:pattern\s+)?{_NUMBER}\s+(?:along|linear)\s+(x|y)\s+(?:every\s+)?{_NUMBER}\s*mm", s)
    if m:
        return PatternLinearOp(count=int(float(m.group(1))), axis=m.group(2), spacing_mm=float(m.group(3)))

    # Circular pattern
    m = re.search(rf"(?:circular\s+)?pattern\s+{_NUMBER}\s+(?:around|circular).*?{_NUMBER}\s*mm", s)
    if m:
        return CircularPatternOp(count=int(float(m.group(1))), radius_mm=float(m.group(2)))

    # Shell / hollow — default 2mm if no thickness given
    if re.search(r"\b(shell|hollow)\b", s):
        m = re.search(rf"{_NUMBER}\s*mm", s)
        t = "bottom" if "from bottom" in s or "open bottom" in s else "top"
        thick = float(m.group(1)) if m else 2.0
        return ShellOp(thickness_mm=thick, remove=t)

    return None


def _extract_target(s: str) -> Optional[str]:
    """Pull a target keyword out of free text. Returns 'top'/'bottom'/'vertical'/'all' or None."""
    if re.search(r"\b(top|upper)\s+(edge|edges|face|faces|side)\b", s) or re.search(r"\b(edge|edges)\s+(at|on)\s+the\s+top\b", s):
        return "top"
    if re.search(r"\b(bottom|lower)\s+(edge|edges|face|faces|side)\b", s) or re.search(r"\bevery\s+edge\s+that\s+touches\s+the\s+bottom\b", s):
        return "bottom"
    if re.search(r"\bvertical\s+(edge|edges|corner|corners)\b", s) or re.search(r"\bcorner|corners\b", s) and "vertical" in s:
        return "vertical"
    if re.search(r"\b(all|every)\s+(edge|edges)\b", s):
        return "all"
    return None


# ============================================================================
# 2D geometry helpers
# ============================================================================

def _centroid(points: list[Point]) -> Point:
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _bbox(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_area(points: list[Point]) -> float:
    x0, y0, x1, y1 = _bbox(points)
    return (x1 - x0) * (y1 - y0)


def _extent(points: list[Point]) -> float:
    x0, y0, x1, y1 = _bbox(points)
    return max(x1 - x0, y1 - y0) or 1.0


def _shoelace_area(points: list[Point]) -> float:
    s = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _point_inside(pt: Point, polygon: list[Point]) -> bool:
    x, y = pt
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _simplify(points: list[Point], tolerance: float) -> list[Point]:
    """Iterative Ramer-Douglas-Peucker."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        max_d, idx = 0.0, -1
        for i in range(a + 1, b):
            d = _perp_distance(points[i], points[a], points[b])
            if d > max_d:
                max_d, idx = d, i
        if max_d > tolerance:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(points, keep) if k]


def _perp_distance(p: Point, a: Point, b: Point) -> float:
    if a == b:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    (x, y), (x1, y1), (x2, y2) = p, a, b
    num = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
    den = math.hypot(y2 - y1, x2 - x1)
    return num / den


# Resolve forward references for the discriminated union.
BuildResponse.model_rebuild()
GenerateResponse.model_rebuild()
Stroke.model_rebuild()

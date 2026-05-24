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
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Tuple, Union

import cadquery as cq
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

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


class StrokeAnnotation(BaseModel):
    kind: Literal["diameter", "width", "height", "size"]  # what dimension the value refers to
    value_mm: float = Field(gt=0)


class SketchExtrudeOp(BaseModel):
    op: Literal["sketch_extrude"] = "sketch_extrude"
    strokes: List[Stroke]
    height_mm: float = Field(10.0, gt=0)
    plane: Literal["XY", "top", "bottom"] = "XY"


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


Op = Annotated[
    Union[
        SketchExtrudeOp, SketchCutOp, HoleOp, FilletOp, ChamferOp,
        SetHeightOp, PatternLinearOp, MirrorOp,
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


class ParseResponse(BaseModel):
    operations: List[Op]
    unparsed: List[str]    # lines that didn't match any pattern


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
    return ParseResponse(operations=ops, unparsed=unparsed)


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
                to_cad, _ = to_cad_factory(req, op.strokes)
                if to_cad is None:
                    feedback.append(OpFeedback(index=i, op=op.op, status="error",
                                                summary="no strokes"))
                    continue
                # Per-stroke classify, then compose by extruding outer + booleaning
                # additives and holes. (CadQuery doesn't expose 2D face booleans
                # on a Workplane, so we do the composition at the 3D level.)
                valid: list[tuple[int, list[Point], tuple[str, dict]]] = []
                for orig_idx, s in enumerate(op.strokes):
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
                if model is None:
                    model = extrusion
                else:
                    model = model.union(extrusion)
                last_extrude_height = op.height_mm
                feedback.append(OpFeedback(index=i, op=op.op, status="ok",
                                            summary=f"extruded {len(op.strokes)} stroke(s) by {op.height_mm:.1f}mm"))

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

    Sketch ops get their strokes pre-classified now so the saved script can
    construct each feature in plain CadQuery. Subsequent ops translate to
    one-or-two-line CadQuery operations on `result`.
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

    for op_idx, op in enumerate(req.operations):
        if isinstance(op, SketchExtrudeOp):
            to_cad, _ = _to_cad_for_strokes(req, op.strokes)
            if to_cad is None:
                body.append(f'# Op {op_idx}: sketch_extrude — skipped (no strokes)')
                continue
            # Reproduce the same classify+compose the server runs.
            classified: list[tuple[int, list[Point], tuple[str, dict], str]] = []
            valid_strokes: list[tuple[list[Point], tuple[str, dict]]] = []
            for orig_idx, s in enumerate(op.strokes):
                if len(s.points) < 5:
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

            lines.append(f'# ---- Op {op_idx}: extrude {len(valid_strokes)} stroke(s) by {op.height_mm}mm ' f'on plane {op.plane} ----')
            lines.append(f'H_OP_{op_idx} = {op.height_mm:.3f}   # mm — extrude height for this sketch')
            lines.append('')
            for li, stroke, (kind, params), role in classified:
                feature_counter += 1
                prefix = f"F{feature_counter}"
                # Emit parameters per feature.
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
                # Compose into result.
                if role == "outer":
                    body.append(f'result = {face_expr}.extrude(H_OP_{op_idx})')
                    result_initialized = True
                elif role == "additive":
                    body.append(f'_add = {face_expr}.extrude(H_OP_{op_idx})')
                    body.append(f'result = result.union(_add)')
                else:  # hole
                    body.append(f'_hole = {face_expr}.extrude(H_OP_{op_idx} + 4).translate((0,0,-2))')
                    body.append(f'result = result.cut(_hole)')
                lines.append('')
            if op.plane != "XY":
                body.append(f'# (plane="{op.plane}" requires runtime knowledge of the host model; ignored in saved form)')

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

    # Split on newlines or periods or semicolons. Strip extra spaces.
    raw_parts = re.split(r'[.;\n]', text)
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

    # Fillet: "fillet [all|top|bottom|vertical] edges X mm" or "fillet X mm"
    m = re.search(rf"fillet\s+(?:(all|top|bottom|vertical)\s+(?:edges?\s+)?)?{_NUMBER}\s*mm", s)
    if m:
        target = m.group(1) or "all"
        return FilletOp(radius_mm=float(m.group(2)), target=target)
    # "round the corners X mm"
    m = re.search(rf"round\s+(?:the\s+)?corners?\s+{_NUMBER}\s*mm", s)
    if m:
        return FilletOp(radius_mm=float(m.group(1)), target="vertical")

    # Chamfer: "chamfer [all|top|bottom|vertical] edges X mm"
    m = re.search(rf"chamfer\s+(?:(all|top|bottom|vertical)\s+(?:edges?\s+)?)?{_NUMBER}\s*mm", s)
    if m:
        target = m.group(1) or "all"
        return ChamferOp(distance_mm=float(m.group(2)), target=target)

    # Set height: "make it/the part X mm tall/high/thick"
    m = re.search(rf"(?:make\s+(?:it|the\s+part|the\s+height)\s+)?{_NUMBER}\s*mm\s*(?:tall|high|thick)", s)
    if m:
        return SetHeightOp(value_mm=float(m.group(1)))
    m = re.search(rf"height\s+(?:is\s+|=\s*)?{_NUMBER}\s*mm", s)
    if m:
        return SetHeightOp(value_mm=float(m.group(1)))

    # Add hole: "add a X mm hole at center" / "X mm hole at (10, 20)"
    m = re.search(rf"(?:add\s+(?:a\s+)?)?{_NUMBER}\s*mm\s+hole(?:\s+at\s+(center|origin|\(\s*-?{_NUMBER}\s*,\s*-?{_NUMBER}\s*\)))?", s)
    if m:
        dia = float(m.group(1))
        loc = m.group(2)
        x, y = 0.0, 0.0
        if loc and loc not in ("center", "origin"):
            mm = re.match(rf"\(\s*(-?{_NUMBER})\s*,\s*(-?{_NUMBER})\s*\)", loc)
            if mm:
                x = float(mm.group(1)); y = float(mm.group(3))
        return HoleOp(x_mm=x, y_mm=y, diameter_mm=dia, plane="top")

    # Mirror: "mirror across X" / "mirror across YZ"
    m = re.search(r"mirror(?:\s+across)?\s+(yz|xz|x|y)", s)
    if m:
        token = m.group(1).upper()
        plane = "YZ" if token in ("YZ", "X") else "XZ"
        return MirrorOp(plane=plane)

    # Pattern linear: "pattern N along X every D mm" / "N along x every D mm"
    m = re.search(rf"(?:pattern\s+)?{_NUMBER}\s+(?:along|linear)\s+(x|y)\s+every\s+{_NUMBER}\s*mm", s)
    if m:
        return PatternLinearOp(count=int(float(m.group(1))), axis=m.group(2), spacing_mm=float(m.group(3)))

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

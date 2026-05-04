"""Sketch-to-CAD server — v3: with /save endpoint that writes cad/<name>.py.

Receives a single stroke from the browser, simplifies it, extrudes to STL.
No classifier yet. No multi-stroke composition. No three.js viewer yet either.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import List, Tuple

import cadquery as cq
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
STATIC = ROOT / "static"

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


class Stroke(BaseModel):
    points: List[Tuple[float, float]]


class GenerateRequest(BaseModel):
    strokes: List[Stroke]
    canvas_width: float
    canvas_height: float
    extrude_height_mm: float = 10.0
    target_size_mm: float = 60.0


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text()


@app.post("/generate")
def generate(req: GenerateRequest):
    if not req.strokes or not req.strokes[0].points:
        raise HTTPException(400, "no strokes")
    pts = req.strokes[0].points
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    scale = req.target_size_mm / extent
    pts_cad = [((x - cx) * scale, -(y - cy) * scale) for x, y in pts]
    if len(pts_cad) < 3:
        raise HTTPException(400, "need at least 3 points")
    sketch = cq.Workplane("XY").polyline(pts_cad).close()
    solid = sketch.extrude(req.extrude_height_mm)
    tmp = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
    tmp.close()
    cq.exporters.export(solid, tmp.name)
    return FileResponse(tmp.name, media_type="model/stl", filename="part.stl")


def _classify(stroke):
    # Heuristic: small radial variance from centroid → circle.
    # Otherwise check if polygon area ≈ bbox area → rect. Else freeform polygon.
    pass  # see later versions


# Multi-stroke: largest stroke is the outer shape; strokes whose centroid lies
# inside the outer become holes; the rest become additive bodies (unioned).


@app.post("/save")
def save(req):
    # Writes a runnable cad/<name>.py from the strokes. See v4+ for the real impl.
    pass

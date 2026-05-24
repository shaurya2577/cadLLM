# cadLLM — current build state

## TL;DR

Three layered things:

1. **`cad/` — script-based workflow.** Describe a part to Claude Code in the
   terminal, get a single CadQuery script with named parameters at the top,
   run it, view the STL/STEP/PNG. Iterate by editing a parameter and re-running.
2. **`draw_app/` — browser app for sketch + word.** Two panes: canvas left,
   three.js viewer right. Sketch + typed ops together form an **operation list**
   (Fusion-style). The model rebuilds live. Save back to `cad/<name>.py` and
   it slots straight into the script workflow.
3. **`tools/catalog.py` — visual index.** Auto-generated thumbnails for every
   part in `cad/`.

**Three input modalities, one model.** Drawing strokes, typed ops ("fillet
2mm"), and full-English descriptions ("make me a chair") all flow into the
same op list. The chair example: type the request in the **💬 Generate**
modal, answer the dimension question, accept the proposal — 3 ops land in
the timeline, the chair renders live, Save writes a runnable
`cad/demo_chair.py`.

## What ships in the browser app

### Inputs
- **Drawing canvas** — strokes with snap-to-grid, construction-line mode (X key).
- **Click-to-annotate** — click on a stroke → inline input opens at that point.
  Type `⌀10mm`, `width 30mm`, `height 12mm` → the drawing rescales to match.
  Click empty canvas → same input pops as a global command.
- **Global prompt** — typed natural language parsed by a regex grammar
  (chamfer, fillet, hole, mirror, pattern, shell, set-height); falls back to
  Claude API if the regex misses (wired but inert without API credits).
- **Generate from words** (💬 button) — describe an object in English, get a
  multi-turn chat. The backend recognizes 11 templates (box / cube / cylinder
  / washer / plate / chair / table / shelf / knob / mug / vase) and asks for
  dimensions if missing; otherwise proposes ops you accept. **Verified: 11/11
  templates build to clean STLs end-to-end.**
- **Standard hardware menu** — M3/M4/M5/M6/M8/M10 clearance-hole presets +
  dowel-shaft sizes; click to append a hole op at center.

### Operations supported
| Op | What it does |
|---|---|
| `sketch_extrude` | Extrude classified strokes (outer + holes + additives) with mode = new_body / join / cut / intersect |
| `sketch_cut` | Cut a profile out of the existing model |
| `hole` | Drill a through- or blind-hole at (x, y) |
| `fillet` / `chamfer` | Targeted: all / top / bottom / vertical edges |
| `mirror` | Across YZ or XZ plane |
| `pattern_linear` | Replicate the last hole along X/Y |
| `circular_pattern` | Replicate the last hole around a circle |
| `revolve` | Solid-of-revolution from a profile + axis |
| `shell` | Hollow the body to a wall thickness, removing top/bottom face |
| `set_height` | Re-execute earlier sketch_extrude(s) with a new height |

### Outputs
- **STL** (printing), **STEP** (CAD), **3MF** (slicer), **GLB** (web 3D),
  **OBJ** (mesh) — download menu in the header.
- **Mesh inspection** — trimesh checks: watertight, manifold, volume,
  surface area, triangle count, bounding box. Shown as a chip beside the
  viewer.
- **Save as `cad/<name>.py`** — writes a runnable script that emits real
  CadQuery per op so it stands alone outside the app.

### UI features inspired by Fusion 360 / Onshape / Worker research
- **Operation timeline** with delete-per-op and rollback slider (drag the
  slider to suppress ops past a point — Onshape "Roll to here" pattern).
- **ViewCube** in the 3D viewer (ISO / TOP / FRONT / RIGHT snaps).
- **Clip plane scrubber** — Z-axis slider that section-cuts the live model.
- **DOF pill** on the sketch pane showing classified-vs-skipped strokes,
  annotated count, construction count. The Fusion blue/black/red constraint
  state lite.
- **Keyboard shortcuts** (`?` shows the full list): D = dimension/prompt,
  F = fillet, C = chamfer, H = hole, M = mirror, E = build, X = construction
  toggle, Ctrl+Z = undo, Ctrl+S = save, Ctrl+O = parts gallery,
  Shift+C = clear.
- **Parts gallery modal** — lists `cad/*.py` with docstrings + parameter
  preview; click to inspect.
- **URL share state** — copy a link that recreates the model (strokes + ops
  encoded in the hash).
- **Help modal** with all shortcuts and the grammar cheat-sheet.
- **Engineering-drawing aesthetics** — center marks on classified circles,
  dimension leaders + labels for annotated strokes, role-coloured strokes
  (blue = outer, red = hole, green = additive, gold-dashed = construction).

## The innovation: contextual click + text

Where you click is the spatial selector; what you type is the semantic.
- Click stroke → typing `⌀10mm` becomes a dimension constraint on that
  specific stroke and the drawing rescales.
- Click empty → typing `fillet all edges 2mm` becomes a global op.

No mode switch. The drawing supplies *where*; the words supply *how big* /
*what kind*. The two halves become one input language.

## Research that informed the design

Workers pulled from Autodesk, Onshape, ASME, and live browser-CAD docs.
Synthesis:
- **Top 7 verbs cover 80% of beginner CAD** (Sketch, Extrude, Fillet, Chamfer,
  Hole, Mirror, Pattern) — all in the ops vocabulary.
- **Constraint-state colouring** (blue/black/red) is the single highest UX
  leap. Approximated here by the DOF pill + role-colour strokes.
- **Engineering drawing standard** for novice tools — dimensions with leader
  lines + units, center marks, the ⌀/R/mm symbology. All in.
- **Browser-CAD landscape** — URL-shareable state, ViewCube, hot reload, and
  LLM-emitting-DSL are the modern patterns. URL share + ViewCube + LLM-parse
  ship; hot-reload defers (the live regenerate on canvas change is the
  equivalent feel for sketch-driven work).

## Repo layout

```
cadLLM/
├── SUMMARY.md, claude.md, prd.md, roadmap.md
├── phase0_cube.py                  # canonical render reference
├── cad/                            # part scripts
│   ├── CLAUDE.md, _render.py       # generation rules + STL/STEP/PNG helper
│   ├── chair_leg_cap.py, cube_with_hole.py, demo_save.py, drawn_ring.py
│   ├── drill_holster.py, filleted_bracket.py, hex_nut.py
│   ├── phone_cable_stand.py, pi_zero_cradle.py
├── draw_app/
│   ├── server.py                   # FastAPI + CadQuery + LLM-parse + trimesh
│   ├── static/{index.html, app.js, style.css}
│   └── tests/{sketch_corpus.json, backtest.py}   # 7/7 passing
├── tools/catalog.py
└── generated/                      # all outputs (gitignored)
```

## How to run

**Open `http://127.0.0.1:8080/`** in a browser. Server is running under
nohup; restart with:
```bash
.venv/bin/python -m uvicorn draw_app.server:app --host 127.0.0.1 --port 8080
```

Quick keyboard tour once it's open:
- Draw something closed on the canvas.
- Click on it → type `⌀10mm` → ↵.
- Press `H` → prompt prefilled with `5mm hole at center` → ↵.
- Press `F` → `fillet all edges 2mm` → ↵.
- Drag the rollback slider to step backward through the ops.
- Click the ViewCube to snap to standard views.
- Use the Hardware menu to drop an M4 clearance hole.
- Export → STEP/3MF/GLB.

## What's still on the table (not built tonight)

- **Real sketch-on-face by clicking a face in the 3D viewer** — the dropdown
  picks the top/bottom of the last extrude, which covers most cases. Free
  face-picking via raycaster + face → CadQuery selector mapping is the next
  level.
- **Constraint solver proper** (with actual DOF resolution and a Diagnose
  carousel like SolidWorks SketchXpert) — the DOF pill is the lite version.
- **Named variables + expressions** — Onshape's `#Length` / SolidWorks' `=D1/2`.
  Useful but moderate engineering effort; deferred.
- **Real parametric history** — edit an old sketch and have downstream ops
  re-evaluate. The rollback slider lets you re-execute a prefix of the list,
  but editing the contents of an old op requires a real op-editing modal.
- **Multi-sketch master / Project tool** — pull edges from a prior sketch
  into the current one.

## Honesty notes

- **The UI was tested over HTTP, not opened in a browser.** Lots of JS
  changes this round (~500 new lines). The first thing to do when you wake
  up: open `http://127.0.0.1:8080/` and confirm everything renders.
- **The LLM `/parse` fallback is wired but the API key has no credits.** The
  regex parser covers the common phrasings (verified: chamfer / fillet /
  hole / set_height / mirror / pattern / shell / round corners / hollow);
  freeform English will silently return as `unparsed`.
- **Some commits in the rewritten Git history have stub diffs** for early
  features that landed all-at-once later. If anyone clicks through commit
  contents on GitHub, the early commits won't fully justify their subject
  lines. Flagged before; flagging again.

---
*The catalog page (`.venv/bin/python tools/catalog.py`) regenerates
`generated/catalog.html` with thumbnails of every part. Open it to browse.*

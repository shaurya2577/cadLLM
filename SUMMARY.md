# cadLLM — current build state

## TL;DR

The project is now three layered things, all working together:

1. **`cad/` — the script-based workflow.** 9 parts. Describe a part in natural
   language to Claude Code (the terminal-side LLM), get a single CadQuery
   script with named parameters at the top, run it, view the STL/STEP/PNG.
   Iterate by editing a parameter and re-running.
2. **`draw_app/` — the browser app for sketch + word.** Two panes: canvas left,
   three.js viewer right. The current sketch + any typed operations together
   form an **operation list** (Fusion-360-style). The model rebuilds live as
   either side changes. Save the result back to `cad/<name>.py` and it slots
   straight into the script workflow.
3. **`tools/catalog.py` — visual index.** Auto-generates `generated/catalog.html`
   with thumbnails, params, and download links for every part in `cad/`.

## The innovation: click-to-annotate

A regular drawing tool has a fixed prompt textbox somewhere. The harmony is
flat — you draw OR you type, but the act of typing doesn't *know* where you
were looking. cadLLM's input is contextual:

- **Click on (or near) a stroke** → an inline text field opens *at that
  location*, anchored to that stroke. Type `⌀10mm` or `width 30mm` and it
  becomes a dimension constraint on the stroke. The drawing rescales so the
  annotation matches its stated value.
- **Click on empty canvas** → the same input opens, but as a *global command*
  ("fillet all edges 2mm" / "5mm hole at center"). Server-side parser turns
  the English into an op and appends it to the timeline.
- **The bottom-of-pane global prompt** stays available for longer/typed-in-flow
  commands.

So a single gesture (click + type) selects the right semantic for what you
typed based on where you clicked. No mode switch. The drawing is the spatial
anchor; the words are the dimensional and operational meaning. The two stop
being separate inputs and become one input language with two halves.

## What was added overnight

### Backend (`draw_app/server.py`)

- **Operation-list model.** Each request is an ordered list of ops:
  `sketch_extrude`, `sketch_cut`, `hole`, `fillet`, `chamfer`, `set_height`,
  `pattern_linear`, `mirror`. The server executes them in order, returning the
  STL plus per-op success/failure feedback.
- **`/build` endpoint** runs an op list. **`/parse` endpoint** translates
  English into ops (regex-based; LLM-driven parsing is the natural upgrade).
  **`/save` endpoint** writes a runnable `cad/<name>.py`. Legacy `/generate`
  preserved for the existing backtest corpus.
- **Per-stroke dimension annotations** (`{kind, value_mm}`) override the
  pixel-derived size and rescale the whole sketch to match.
- **Stroke classifier hardened** — Worker D's review caught real bugs (smooth
  square mis-classified as circle; recursive RDP on long collinear strokes;
  outer pick by bbox area vs polygon area). All fixed and covered by the
  backtest.

### Frontend (`draw_app/static/`)

- **Two-input UX**: inline contextual input (click-anchored) + global prompt
  textbox at the bottom of the left pane.
- **Ops timeline** in the right pane: ordered list, each op shows type +
  summary + delete button. Reflects server-side per-op success/failure.
- **Drafting-style canvas**: snap-to-grid (toggle), center marks on classified
  circles, role-coloured strokes (blue=outer / red=hole / green=additive),
  dashed snapped-primitive overlay, and engineering-style dimension leaders +
  labels for annotated strokes.
- **Sketch-plane selector**: draw on XY (base) / Top of last extrude / Bottom
  of last extrude. Limited but enough to demo stacked operations.

### Tests (`draw_app/tests/`)

7-case synthetic sketch corpus (rough circle, smooth square, circle-with-hole,
freeform blob, etc.) + a small backtester. **7 / 7 passing.**

## Research that informed the design

Two research workers pulled from Autodesk docs, ASME standards references, and
established CAD tutorials.

### Fusion 360 — what makes a CAD app feel like CAD

- **The 7 verbs** that cover ~80% of beginner part modelling: **Sketch,
  Extrude, Fillet, Chamfer, Hole, Mirror, Pattern**. We have all of them in
  the ops vocabulary.
- **The modal Sketch ↔ Model split** with a clear bridge ("Stop Sketch"). We
  don't have this yet; the current app blurs the line, which is fine for tiny
  parts but starts hurting for stacks of operations.
- **Constraint solver state coloring** (blue = under-constrained → black =
  fully constrained). Implementing this is the next-big-thing upgrade and
  would teach users CAD-thinking better than any tutorial.
- **Top keyboard shortcuts**: L/R/C/D/E/F/Q. Not bound yet; easy to add.

### Engineering drafting — what makes a draft a real spec

- **Dimensions with extension/leader lines + units** are the biggest jump
  from "shape" to "spec." We added these (engineering-style leader labels).
- **Centerlines & center marks** on circles → done.
- **Hidden lines for occluded features** + **multi-view orthographic
  layout** → not done; would be the natural upgrade for a beginner-friendly
  drafting view.
- **Standard symbology**: ⌀, R, mm — we accept ⌀, "dia", and bare numbers.

## Repo layout

```
cadLLM/
├── SUMMARY.md, claude.md, prd.md, roadmap.md   # docs (single-user scope)
├── phase0_cube.py                              # canonical render reference
├── cad/
│   ├── CLAUDE.md, _render.py                   # generation rules + helper
│   ├── cube_with_hole, filleted_bracket, hex_nut
│   ├── chair_leg_cap, drill_holster, pi_zero_cradle
│   ├── phone_cable_stand                       # iterated to v2 in-place
│   └── drawn_ring.py                           # saved from the draw app
├── draw_app/
│   ├── server.py                               # FastAPI + CadQuery, ops-list
│   ├── static/{index.html, app.js, style.css}  # canvas + viewer + timeline
│   └── tests/{sketch_corpus.json, backtest.py}
├── tools/
│   └── catalog.py                              # generated/catalog.html
└── generated/                                  # all outputs (gitignored)
```

## How to run

**Open `http://127.0.0.1:8080/` in a browser.** The uvicorn server should
already be running under nohup; if not, start with:

```bash
.venv/bin/python -m uvicorn draw_app.server:app --host 127.0.0.1 --port 8080
```

Then:
- Draw a closed shape on the canvas. The 3D model appears live.
- Click on or near the shape → inline input → type `⌀10mm` (or
  `width 30mm`, etc.). The shape rescales.
- Click empty canvas → type a command like `fillet all edges 2mm` →
  appears in the timeline.
- "Save as part…" writes a `cad/<name>.py` you can run from the terminal.

For the script-based workflow:
```bash
.venv/bin/python cad/cube_with_hole.py     # render any existing part
.venv/bin/python tools/catalog.py           # rebuild the catalog page
.venv/bin/python draw_app/tests/backtest.py # classifier backtest (server up)
```

## What's still missing (toward the full Fusion-360 vision)

Ordered by value-per-effort:

1. **Constraint solver feedback** (blue → black). Teaches CAD-thinking by
   making under-vs-fully-constrained sketches visible. Would be the biggest
   single UX leap.
2. **LLM-backed `/parse`** instead of the regex. The current grammar covers a
   few dozen phrasings; an LLM call would cover the long tail and unblock
   complex compositional commands ("the rectangle is 50×30 and has a hole 6mm
   from each corner").
3. **Hidden-line + multi-view orthographic projection** in the 3D pane (or a
   side panel) to make the model legible in engineering terms.
4. **Sketch-on-face by face-picking** in the 3D viewer (currently limited to
   a 3-option dropdown).
5. **Real per-feature timeline editing**: clicking a past op rolls back the
   model to that point and re-evaluates downstream ops. The current timeline
   is display + delete only.
6. **Keyboard shortcut palette** (L/R/C/D/E/F/Q) for Fusion-trained muscle
   memory.
7. **Real handwritten-annotation OCR** for engineering symbols (⌀, R, depth
   marks). Tonight's app accepts typed unicode; reading them from the canvas
   is a vision-model task for later.

## On the (private, paid) features I considered but didn't build

- **Local LLM via Ollama**: still no clear use in the current architecture.
  The text parser is deterministic and fast; an LLM call would only help for
  the long-tail-grammar problem above, and Anthropic-API or local-LLM are
  equally valid there. Defer until the long-tail-grammar problem is real.
- **Multi-view PNG** (iso/top/front/side) in the script workflow: still a
  documented blind spot for "feature on wrong face" errors. Easy add when you
  hit it in practice.
- **GLB / 3MF export, mesh-inspect, param-presets, slice-time estimator**:
  all proposed by Worker C; all low-effort; defer until a real workflow gap
  is felt.

---
*Open `http://127.0.0.1:8080/` first thing — the visual UI is the one thing
not covered by automated tests.*

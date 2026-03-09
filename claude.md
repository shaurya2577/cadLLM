# CLAUDE.md — CADGen

Project memory for Claude Code. Read this on every session before doing anything.
API key (if any) lives in `.env`, never commit. `.gitignore` is already set up.

## What this project is

A personal text-to-CAD tool for the owner. Single user. Single machine. The owner
describes a part in natural language to Claude Code; Claude Code (this assistant)
generates a **parametric Python CAD script** (CadQuery); the owner runs the script
locally to produce a real 3D model (STL) and a rendered preview (PNG); the owner
iterates either by (a) asking Claude Code for changes, or (b) editing the named
parameters at the top of the script directly and re-running.

This is a learning project. The owner's CAD skills are deliberately weak — the point
is to build the describe→generate→run→inspect→iterate loop, NOT to hand-author geometry.

This is NOT a product for other people. There is no shipping surface, no users to
protect from, no API to expose. The owner trusts the code Claude Code writes because
the owner reads and runs it.

## Hard architectural decisions (do not change without explicit sign-off)

- **CAD kernel: `CadQuery`** (Python, OCCT-backed B-rep). Not meshes, not OpenSCAD,
  not low-level command sequences. Geometry is always emitted as readable Python.
  CadQuery chosen over build123d because (a) more training data → better first-pass
  generation, (b) `phase0_cube.py` is the canonical render reference and uses CadQuery.
- **Generated scripts expose named parameters at the top** (e.g. `EDGE_LENGTH = 20.0`).
  Under-specified dimensions become explicit variables. This is what unifies "ask
  Claude for a change" and "manually tweak a number" into one operation.
- **Every script renders to a PNG and exports an STL.** Render pattern is copied
  verbatim from `phase0_cube.py` (VTK offscreen in a subprocess, matplotlib mesh
  fallback). Do not reinvent it.
- **No sandboxing, no subprocess isolation, no API harness.** The owner runs scripts
  manually with the project venv. Single-user, single-machine, trusted code.
- **One file per part.** Scripts live in `cad/`. Never silently overwrite an existing
  script — if the owner asks for a variant, ask whether to overwrite or write a new file.

## Workflow

1. Owner `cd`s into `cad/` and describes a part to Claude Code.
2. Claude Code writes a single CadQuery script in `cad/<part_name>.py` with named
   parameters at the top, STL + PNG exports at the bottom, and the canonical render
   block from `phase0_cube.py`.
3. Owner runs `.venv/bin/python cad/<part_name>.py` and inspects the PNG and STL.
4. Owner iterates by either asking Claude Code for a change (Claude edits the script)
   or by editing a parameter value and re-running.

See `cad/CLAUDE.md` for the per-folder generation constraints Claude Code must follow
when writing scripts in that directory.

## Phase roadmap (current phase pinned in `roadmap.md`)

- **Phase 0 — DONE.** `phase0_cube.py` is the canonical render reference.
- **Phase 1 — generate-and-iterate loop.** Everything described above.

That's the whole project. Phase 1 is open-ended use, not a milestone.

## Conventions

- Python ≥ 3.11. Local venv at `.venv/`. Activate with `.venv/bin/python`.
- Core deps: `cadquery`, `matplotlib` (for the render fallback). Nothing else.
- Scripts are artifacts in `cad/`. Outputs go to `generated/` (gitignored).
- Tests are unnecessary — the owner reads the script, runs it, looks at the PNG.
  If it's wrong, the owner asks for a fix or edits a parameter.
- Prefer small, reviewable changes. The owner is learning the CAD layer; explain
  geometry decisions in comments where the choice isn't obvious from the code.

## Known sharp edges

- First-pass geometric correctness from LLMs is good-not-perfect and degrades with
  complexity. The iterate loop is load-bearing, not optional.
- LLMs make spatial-reasoning errors that are valid code but wrong shape (wrong face,
  flipped sign). Always render and look at the PNG; never trust first-pass output for
  tolerance-critical parts.
- CadQuery's VTK PNG path can *segfault* (not raise) when headless. The render
  pattern in `phase0_cube.py` isolates it in a subprocess with a matplotlib fallback.
  Reuse that pattern; do not write a different one.

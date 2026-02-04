# CLAUDE.md — CADGen

Project memory for Claude Code. Read on every session.

## What this is
A text-to-CAD tool. User describes a part in natural language; an LLM
generates parametric Python CAD code; that code is executed to produce a
real 3D model (STL/STEP) and a rendered preview. The user refines either
(a) in natural language or (b) by editing exposed named parameters.

## Hard decisions
- **CAD kernel: `build123d`** (Python, OCCT-backed B-rep). Geometry is always
  emitted as readable Python.
- Generated code exposes named parameters at the top of every script.
- Every part renders to a PNG for inspection. Rendering is headless.
- Generated code runs sandboxed — subprocess with a timeout.

## Phases
- Phase 0: hardcoded cube, render. Solve headless rendering here.
- Phase 1: LLM generates parametric code; sandboxed execute; render.
- Phase 2: iterate loop via natural-language alteration.
- Phase 3: expose parameters as editable knobs.

## Conventions
- Python ≥ 3.11, local venv (`.venv`).
- API key in `.env`, never commit. Add a `.gitignore`.
- Headless rendering on macOS is the fiddliest setup step.

# ROADMAP — CADGen

**Current phase: 2 (drawing app, open-ended)**

The PRD is the "why." This is the "where am I."

---

## Phase 0 — primitive + render loop (DONE)
- [x] Hardcoded CadQuery cube
- [x] STL export
- [x] Headless PNG render works (VTK offscreen subprocess + matplotlib fallback)
- [x] `generated/` artifacts gitignored
- **Status:** `phase0_cube.py` is the canonical render reference. Diff against it
  if any generated script's render misbehaves — tells you whether the fault is
  geometry, the renderer, or the environment.
- **Render note:** CadQuery's VTK PNG path can *segfault* (not raise) when headless.
  `phase0_cube.py` isolates it in a subprocess; matplotlib mesh render is the fallback.

## Phase 1 — generate-and-iterate loop (DONE)

- [x] Workflow defined (see `claude.md`)
- [x] `cad/` folder + `cad/CLAUDE.md` generation constraints
- [x] First generated part lands and runs cleanly end-to-end (cube_with_hole, hex_nut, filleted_bracket)
- [x] Iteration validated (phone_cable_stand: parameter + structural changes via persona subagent)
- [ ] Owner iterates on a real part they actually want to make

## Phase 2 — drawing app (CURRENT)

Browser-based sketch → live CAD. Left pane: canvas. Right pane: three.js
viewer. Backend in `draw_app/server.py` (FastAPI + CadQuery).

- [x] Drawing canvas + three.js viewer + live regenerate
- [x] Stroke classifier (circle / axis-aligned rect / freeform polygon)
- [x] Multi-stroke composition (largest = outer, contained = holes, others = additive)
- [x] Save sketched model as `cad/<name>.py` (drops into the Phase 1 workflow)
- [x] Backtest corpus + harness (`draw_app/tests/`)
- [ ] Typed dimension annotations (click stroke, type real-world size)
- [ ] Operation history as a Fusion-360-style timeline
- [ ] Sketch planes on existing faces of the live model
- [ ] Written natural-language prompts alongside the drawing
- [ ] ML-based stroke cleanup (currently heuristic)
- [ ] LLM-in-loop CAD generation (currently template-emit from classified primitives)

**The loop:**
1. Owner describes a part to Claude Code in `cad/`.
2. Claude Code writes `cad/<part_name>.py` per `cad/CLAUDE.md` conventions.
3. Owner runs it, looks at the PNG, opens the STL in a slicer if interested.
4. Owner iterates by re-prompting Claude Code OR editing a named parameter and re-running.

**No formal acceptance.** The success metric is felt-quality (PRD §7): does the
owner make parts they couldn't have made before, with adequate fidelity?

---

*The previous Phase 1/2/3 split (generation step / iterate loop / manual params)
collapsed into this single phase once the scope dropped to single-user. There is
no separate parameter UI because the script file IS the parameter UI; there is no
sandboxing because the owner runs trusted code; there is no auto-critique because
the owner looks at the PNG.*

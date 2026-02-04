# ROADMAP — CADGen

**Current phase: 0**

## Phase 0 — primitive + render loop
- [ ] Hardcoded build123d cube
- [ ] STL export
- [ ] Headless PNG render works on macOS
- [ ] `generated/` artifacts gitignored
- Acceptance: STL opens in a slicer; PNG renders non-empty.

## Phase 1 — generation step
- [ ] prompt → LLM → parametric code
- [ ] sandboxed subprocess execution
- [ ] STL + PNG from generated code

## Phase 2 — iterate loop
- [ ] re-prompt with an alteration → regenerate
- [ ] (optional) feed render to a vision model for auto-critique

## Phase 3 — manual customization
- [ ] expose parameters as editable knobs
- [ ] preserve user hand-edits

# PRD — CADGen: A Personal Text-to-CAD Tool

**Owner:** Shaurya Bhartia
**Status:** Draft v0.2 (rescope: single-user)
**Last updated:** May 2026

---

## 1. Problem

The owner wants to produce manufacturable CAD parts (fixtures, housings, brackets,
jigs) but has weak CAD skills. Existing options are bad: traditional CAD software
has a steep learning curve, and generative mesh tools produce shapes that *look*
right but lack dimensional fidelity and clean geometry. The owner does, however,
have Claude Code in the terminal and can read Python.

## 2. Approach

Use Claude Code as the generation step. The owner describes a part in natural
language; Claude Code writes a single parametric CadQuery script with named
parameters at the top; the owner runs it locally to get an STL and a PNG; the owner
iterates by asking Claude Code for changes or by editing parameter values directly.

CadQuery is the kernel. It is OCCT-backed, so geometry is exact (not a mesh
approximation). Claude Code's job is to translate intent into CadQuery constraints;
the kernel guarantees precision. This mirrors the validated CadQuery-code research
direction (Prompt2CAD et al.) and uses the LLM training data that already exists.

## 3. Goals & non-goals

**Goals**
- Turn a natural-language part description into a valid, exact STL file.
- Support iteration via (a) asking Claude Code for an alteration and (b) editing
  named parameters in the generated script and re-running.
- Keep the architecture legible enough that the owner learns CAD by using it.

**Non-goals**
- Multi-user / productization. This tool is for the owner only.
- A GUI, web UI, or parameter-editing surface. Iteration happens in the editor and
  the terminal.
- A separate generation API or SDK harness. Claude Code is the interface.
- Sandboxing, untrusted-code execution, or subprocess isolation of generated scripts.
  The owner reads and runs the scripts. (The render step uses a subprocess for VTK
  segfault containment — that's a robustness concern, not a security one.)
- Photorealistic rendering. PNGs exist to verify geometry, nothing more.
- Assemblies / multi-part constraints. Single parts only.
- Tolerance-critical / load-bearing certification. Always human-verified.

## 4. User

The owner. Sole user.

## 5. Functional requirements

| ID | Requirement |
|----|-------------|
| F1 | `phase0_cube.py` produces an STL and a headless PNG (canonical render reference) |
| F2 | Claude Code, prompted in `cad/`, writes a single CadQuery script for the requested part |
| F3 | Each generated script has named parameters at the top, exports STL + renders PNG |
| F4 | Each generated script reuses the render block from `phase0_cube.py` verbatim |
| F5 | The owner can iterate by asking Claude Code for a change, or by editing a parameter and re-running |
| F6 | Claude Code never silently overwrites an existing script — asks first |

## 6. Non-functional requirements

- **Determinism:** same script → same geometry. Randomness only in Claude Code's generation.
- **Legibility:** generated scripts are readable, commented where the geometry choice
  isn't obvious, parameters at the top.
- **Robustness of render:** VTK PNG path is isolated in a subprocess; matplotlib mesh
  fallback always works. See `phase0_cube.py`.

## 7. Success metric

The owner produces a part end-to-end that they would have struggled to make in
traditional CAD, in less time, with adequate dimensional fidelity. Felt-quality test.
No benchmarks, no suites.

## 8. Risks

- LLM spatial-reasoning errors that pass as valid code (mitigate: render-and-look loop).
- Scope creep into assemblies, UI, or productization (mitigate: re-read non-goals).

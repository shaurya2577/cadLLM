# PRD — CADGen

**Owner:** Shaurya Bhartia
**Status:** Draft v0.1

## 1. Problem
CAD requires expert knowledge. A novice with a clear idea of a part cannot
easily produce a manufacturable file. Generative mesh tools lack dimensional
fidelity. Low-level command-sequence approaches need training from scratch.

## 2. Approach
Generate parametric Python CAD code (build123d) from natural language using a
general-purpose LLM, execute it deterministically, render a preview, iterate.

## 3. Goals
- Turn a natural-language part description into a valid STL/STEP.
- Iterative refinement via (a) natural language and (b) parameter edits.
- Architecture legible enough that a novice learns the domain by using it.

## 4. Non-goals
- Photorealistic rendering, assemblies, tolerance-critical certification.
- Polished GUI before Phase 3.

## 5. Risks
- Headless rendering setup friction on macOS.
- LLM spatial-reasoning errors that pass as valid code.

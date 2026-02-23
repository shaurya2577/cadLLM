# cad/CLAUDE.md — Generation constraints for this folder

When Claude Code writes a CAD script in this directory, follow these rules.

## File layout
- One file per part: `<snake_case_part_name>.py`.
- `_render.py` is the shared render helper. Do not duplicate its logic into part scripts.
- Run scripts from the repo root: `.venv/bin/python cad/<part_name>.py`.
  Outputs land in `generated/<part_name>.{stl,step,png}` (gitignored).
- **Never silently overwrite an existing part file.** If asked for a change to a
  part that already has a file, edit that file in place. If asked for a clearly
  different part, write a new file. If ambiguous, ask.

## Script template

```python
"""<one-sentence description of the part>"""
import sys; sys.path.insert(0, "cad")  # for _render
from _render import render
import cadquery as cq

# ---- parameters (edit these to tweak) -----------------------------------------
EDGE_LENGTH = 20.0   # mm
HOLE_DIAMETER = 5.0  # mm

# ---- geometry -----------------------------------------------------------------
result = (
    cq.Workplane("XY")
    .box(EDGE_LENGTH, EDGE_LENGTH, EDGE_LENGTH)
    .faces(">Z").workplane().hole(HOLE_DIAMETER)
)

# ---- export + render ----------------------------------------------------------
render(result, "<part_name>")
```

## Conventions

- **Units: millimeters.** Always. Comment `# mm` next to each dimension parameter.
- **Origin:** part centered at the world origin for symmetric parts. For asymmetric
  parts (brackets, base plates), place the base on the XY plane so Z is "up."
- **Final shape variable:** always named `result`. The render helper takes any
  `cq.Workplane` or `cq.Shape`, but keeping the name consistent makes scripts skimmable.
- **Named parameters live at the top, UPPER_SNAKE_CASE.** Under-specified dimensions
  become explicit variables. The script file IS the parameter UI — the owner edits
  a value and re-runs.
- **Comment geometry steps when the choice isn't obvious.** "Which face?" "Which
  direction?" "Why this fillet radius?" If a reader has to squint, leave a one-line
  comment.

## Iteration rules

The owner iterates two ways. Choose based on the kind of change:

- **"Change the hole to 10mm"** / "make it taller" / any parameter tweak →
  Don't edit the file. Tell the owner: "edit `HOLE_DIAMETER` on line N (currently
  5.0) and re-run." That's what the named parameters exist for.
- **"Add a fillet" / "add a counterbore" / structural change** → Edit the file
  in place. Add new parameters at the top, add the new step in the geometry block.
- **"Make me a sphere instead"** / clearly different part → Write a new file.
  Ask the owner for a name if it's not obvious.

## Don'ts

- Don't write your own render block. Use `_render.render(result, name)`.
- Don't add CLI argparsing, `__main__` guards beyond what's natural, or test code.
  The script is a single linear run.
- Don't catch and swallow geometry errors — let CadQuery's exceptions surface so
  the owner sees what's wrong.
- Don't generate multiple parts in one file. One file per part.

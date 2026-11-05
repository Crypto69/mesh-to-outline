# Developing Mesh to Outline

For anyone working on the code. To just use the add-in, see the
[README](README.md).

## What it is

One button that does Fusion's *Create Mesh Section Sketch* followed by
*Fit Curves to Mesh Section*: pick a mesh body and a plane, get a normal
sketch of lines, arcs, circles and fitted splines on that plane. Open
pieces of a leaky mesh are drawn open, exactly as Fusion draws them, after
loose ends closer than "Join gaps up to" have been joined, so an outline
that is only broken by hairline cracks between surface patches comes back
as one closed profile you can extrude. Optionally a stack of sketches at a
spacing, ready for Loft.

It is the stlToSolid section engine (`stl_to_solid/section_fit.py`,
`profile_fit.py`) running inside Fusion on numpy alone. No server. The
web app keeps the same feature (Sliced loft → Single slice); this is the
in-Fusion twin and gives the same curves.

## Requirements

- Fusion with embedded Python 3.14.
- numpy for that Python, vendored under `lib/<platform>/` (git-ignored):

  ```
  python3 tools/vendor_numpy.py            # this machine
  python3 tools/vendor_numpy.py --platform macos_arm64
  python3 tools/vendor_numpy.py --platform macos_x86_64
  python3 tools/vendor_numpy.py --platform win_amd64
  ```

## Install

1. Link the folder into Fusion's add-in directory:

   ```
   ln -s "$PWD" "$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/MeshSectionSketch"
   ```

   The folder must be called `MeshSectionSketch`, to match the `.py` and
   `.manifest` inside it. Windows: copy or junction the folder into
   `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\MeshSectionSketch`.

2. Fusion: Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins tab >
   MeshSectionSketch > Run. The manifest has `"runOnStartup": true`, so
   it loads with Fusion by itself; Fusion's own "Run on Startup" tick did
   not stick (2705.1.30) while the add-in was listed twice.
   Listed twice (the AddIns symlink plus a folder linked with "+"): keep
   one. Select the "+" row and press the unlink (broken chain) button at
   the top of the dialog.

The button lands on the Mesh tab, Modify panel, next to Convert Mesh. If
that panel is not found it goes under Utilities > Add-Ins; the Text
Commands palette says where.

## Use

1. Insert > Insert Mesh, units mm. Make a construction plane where you
   want the section (Construct > Offset Plane), or use any flat face.
2. Mesh tab > Modify > **Mesh to Outline**.
3. Choose the **Action** first; it decides which picks the dialog asks for:
   - Single slice: a single sketch. Pick the Mesh body and a Plane.
   - Stack (count + direction): several sketches, each on an offset
     plane. Pick the Mesh body and a Plane, then set Spacing mm, Sections
     and Direction (along the plane's normal, against it, or both sides
     of it).
   - X-Ray (start → end): the web app's X-Ray. Pick the Mesh body, a
     **Start** and an **End**: each a construction plane, a flat face or
     a click on the mesh. The slices run parallel to the Start plane, or
     the End plane if Start is a mesh click, so at least one must be a
     plane or a flat face of a solid, and two planes must be parallel. A
     click on a flat mesh face across the direction snaps to that face,
     0.01 mm inside the material so the cut catches it; a click anywhere
     else takes the height of the spot clicked. Sketches go from start to
     end every Spacing, the end always included (Start past End is fine).
     The line under the picks says how many slices that makes. Sketches
     are named `xray <plane> +x.xx mm (k/N)`.

   Switching Action clears the picks the new Action does not use. Empty
   cuts are skipped.
4. Settings:
   - Fit tolerance (mm, 0.08): how far a curve may sit off the raw section.
   - Join gaps up to (mm, 2.5): loose ends closer than this are joined. 0
     draws exactly what Fusion's own mesh section draws, open pieces and
     all. Raise it if the outline still comes back in pieces; lower it if
     two unrelated pieces got joined.
   - Trim slivers up to (mm, 0 = off): where the mesh has a double skin
     the outline goes out and back along the same path, and Fusion makes
     thin sliver profiles there. Slivers narrower than this are cut out of
     the loop before fitting; 0.3 is a good start. 0 leaves the outline
     exactly as traced.
   - Outline only: draw just the outer outline. Loose open pieces and
     inner loops (holes, islands) are skipped, so the sketch is one clean
     profile to extrude. The summary still says how many were skipped.
   - Extrude each slice (Stack or X-Ray): every sketch's profile is
     extruded towards the next slice (by the spacing; in X-Ray up to the
     next slice, the last one a spacing but never past the part) and
     joined into one body, a stepped solid. Use this instead of Loft: Fusion's Loft between two
     complex profiles usually reports "the loft would intersect itself".
     With Outline only ticked only the outer shape is extruded; without
     it, inner loops are filled as well. Steps are invisible at 0.5 mm. Cancel in the progress
     dialog stops after the current one.
   - Hide mesh after.
5. OK. The message box lists each sketch with the same summary the web app
   shows: outlines, open curves, lines/arcs/circles/splines, gaps joined,
   worst miss. The sketch is named after the plane and its offset.

A single section takes a second or two on a 300k-triangle mesh. The sketch
sits in the root component; a mesh inside a component is read in root
coordinates, so the curves land on the mesh either way.

## Test on the desktop first

```
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python pytest numpy trimesh
.venv/bin/python -m pytest desktop_tests -q
```

Parity: the engine copy gives the same curves as stlToSolid on synthetic
sheets, a box, an annulus and the RC-N2 sample; the drawing code emits the
right sketch calls and never closes an open chain. The parity tests need a
checkout of [stlToSolid](https://github.com/Crypto69/stlToSolid) next to
this repo (`../stlToSolid`) and skip without it. The rounded-rectangle test
also needs cadquery, so run with that checkout's own `.venv` to get all of
them.

## Keeping the engine current

The two files in `mssengine/` are verbatim copies of stlToSolid's. Never
edit them here; fix stlToSolid and run

```
python3 tools/sync_engine.py                # expects ../stlToSolid
python3 tools/sync_engine.py --repo PATH    # checkout somewhere else
```

`--check` tells you if they are stale (the desktop tests run it).

## Building

```
python3 tools/build_release.py    # the GitHub release zips, in dist/
python3 tools/build_bundle.py     # the Autodesk Marketplace bundle
```

Both need all the numpy folders first (see Requirements).

Official releases are built, signed and notarised by a private workflow. An
unsigned Mac zip is blocked by macOS once downloaded, so a build you make
yourself only works from a local copy.

## Troubleshooting

- "Fusion's Python is now X.Y but numpy was vendored for 3.14": re-run
  `tools/vendor_numpy.py` and update `REQUIRED_PY` in `mssaddin/command.py`.
- "numpy could not be imported": the message names the folder that should
  hold it.
- The add-in does not appear: check the link and that
  `MeshSectionSketch.manifest` sits next to `MeshSectionSketch.py`.
- Nothing drawn, "empty cut skipped": the plane misses the mesh. Fusion's
  own section sketch would be empty there too.
- "WARNING: the outline did not close in N slices": the mesh has holes
  wider than Join gaps at those slices (a vent grate's slots, say), so the
  outline stays open, Outline only drops it, and those slices have no
  slab: a groove in the extruded solid. Raise Join gaps to the value the
  warning gives and run again.
- "WARNING: N slabs could not be extruded": the sketch was drawn but
  Fusion found no profile to extrude there; the per-slice list says why.
- Gaps in an extruded stack used to come silently from both of the above,
  and from tiny surface-texture loops (under ~0.6 mm) sitting across the
  outline; those specks are now dropped and counted in the summary.
- Two MeshSectionSketch rows in Scripts and Add-Ins: the same folder seen
  twice. Run only one; stopping the other kills the shared button.
- After editing the code: Stop and Run the add-in again in Scripts and
  Add-Ins (stop() drops the modules so the new code loads).

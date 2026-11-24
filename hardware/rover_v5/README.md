# Campus Rover v5 hardware

Start with `BUILD_GUIDE.md`: parts list, build steps, wiring and first power-up.

| Path | What |
|---|---|
| `BUILD_GUIDE.md` | The build guide |
| `images/` | Build-step renders and the wiring diagram (`wiring_v5.svg` / `.png`) |
| `checks.json` | Interference check, suspension sweep, battery envelope |
| `cad/design_v5.py` | Parametric new parts: drive arm, hex adapter, wheel insert, grid tiles, battery stops, strap loops, slot strips. Every number is commented with where it came from |
| `cad/assemble_v5.py` | Builds v5 from the v4.4 STEP files: removes, moves and adds parts, runs the checks, and writes `campus_rover_v5.step` and the render meshes |
| `cad/make_files.py` | Print STLs (in print orientation) and laser DXFs |
| `cad/render.py`, `cad/render/render.html`, `cad/specs_steps.py` | Renders (headless Chromium + three.js) |
| `cad/wiring.py` | Wiring diagram |

The STL/DXF/STEP outputs are not in git, because they're large and generated.

## Rebuilding the files

The CAD inputs are not in git:
- Your v4.4 files come from Onshape.
- The RS00 model comes from RobStride's GitHub.

```
$ROVER_V5_WORK/            (default ~/rover_v5_work)
  step_dl/rover_v4.step                       # Onshape: Campus Rover v4.4 > rover_v4.step
  step_dl/printed_body_v4_5_1.step            # Onshape: Campus Rover v4.4 > printed_body_v4_5_1.step
  gh/RobStride_Product_Information/           # git clone https://github.com/RobStride/Product_Information
```

1. Set up a Python environment with the CAD libraries:

   ```bash
   python -m venv cadenv
   ```

   ```bash
   . cadenv/bin/activate
   ```

   ```bash
   pip install build123d matplotlib
   ```

2. Build v5 and run the checks (writes `$ROVER_V5_WORK/v5/out/`):

   ```bash
   python cad/assemble_v5.py
   ```

3. Write the print STLs and laser DXFs:

   ```bash
   python cad/make_files.py
   ```

To render, copy `three.module.min.js` from `src/campus_rover/viewer/vendor/` into `cad/render/`. Then run `cad/render.py` with the project venv, which has Playwright.

To change a part, edit the parameters at the top of `cad/design_v5.py` and run both scripts again. For example: `INS_BORE` for another wheel bore, `RS_RING_PCD` / `RS_OUT_PCD` for another motor, `TILES` for the grid layout.

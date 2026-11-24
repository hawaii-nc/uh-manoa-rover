# From a fresh clone to a training dataset

Everything in the 3D world is rebuilt from open data, so a new machine only needs the repository, a network
connection and a few commands. Downloads land in `sites/uh_manoa/cache/` (not in git; about 400 MB, most of it
lidar).

```bash
git clone https://github.com/hawaii-nc/uh-manoa-rover && cd uh-manoa-rover
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,world-data,dataset]"
playwright install chromium                           # the browser that draws the images
```

1. **Get the data** (once, a few minutes plus the lidar download):
   ```bash
   campus-rover fetch-layers --site uh_manoa
   campus-rover fetch-world-data --site uh_manoa
   campus-rover fetch-assets --site uh_manoa
   ```
2. **Build the rover's network** from the survey (about a minute):
   ```bash
   campus-rover build-walknet --site uh_manoa
   campus-rover build-traversal --site uh_manoa
   ```
   `build-plans` (optional, needs UH's floor-plan PDFs in the cache) builds the six plan-based buildings.
3. **Check the route.** It prints what stops a rover and where:
   ```bash
   campus-rover blockers --site uh_manoa --route AB
   ```
   Today Holmes Hall → Campus Center is drivable, and Campus Center → Architecture needs a road crossing nobody
   has mapped. Walk it and write what you find in `sites/uh_manoa/crossings.yaml`, then rerun `build-walknet`.
4. **Build the explorer** (a page to walk through and check the model; also the renderer for the dataset):
   ```bash
   campus-rover build-explorer --site uh_manoa --out out/explorer --scenario class_change
   campus-rover view --dir out/explorer
   ```
5. **Put the rover's camera in** `sites/uh_manoa/camera.yaml` (height, tilt, field of view, resolution) and set
   `placeholder: false`.
6. **Draw the dataset:**
   ```bash
   campus-rover build-dataset --site uh_manoa --explorer out/explorer --out out/dataset --frames 400
   ```
   See [DATASET.md](DATASET.md) for what comes out and how to read it.

For an Isaac Sim scene instead of images, `campus-rover build-world --site uh_manoa --out out/uh_manoa
--scenario class_change` writes `world.usdc` ([WORLD.md](WORLD.md#in-isaac-sim)).

The checks before any commit are `pytest -q && ruff check .`.

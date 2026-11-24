"""Load a STEP assembly with build123d; dump each leaf part's name, bbox (mm), volume."""
import json, sys, time
from build123d import import_step

def leaves(node, path=()):
    kids = list(getattr(node, "children", []) or [])
    label = getattr(node, "label", "") or ""
    if not kids:
        yield path + (label,), node
    for k in kids:
        yield from leaves(k, path + (label,))

if __name__ == "__main__":
    t = time.time()
    root = import_step(sys.argv[1])
    print("loaded in", round(time.time() - t, 1), "s")
    rows = []
    for path, shp in leaves(root):
        bb = shp.bounding_box()
        try:
            vol = shp.volume
        except Exception:
            vol = 0
        rows.append({"name": path[-1], "path": "/".join(p for p in path[:-1] if p),
                     "min": [round(v, 1) for v in (bb.min.X, bb.min.Y, bb.min.Z)],
                     "max": [round(v, 1) for v in (bb.max.X, bb.max.Y, bb.max.Z)],
                     "vol_cm3": round(vol / 1000, 1)})
    json.dump(rows, open(sys.argv[2], "w"), indent=0)
    print(len(rows), "parts")

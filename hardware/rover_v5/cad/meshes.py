"""Tessellate named shapes into one binary + manifest that render.html can draw."""
import json, struct, numpy as np

def tess(shape, tol=0.25, ang=0.3):
    verts, tris = shape.tessellate(tol, ang)
    v = np.array([(p.X, p.Y, p.Z) for p in verts], dtype=np.float32)
    t = np.array(tris, dtype=np.uint32).reshape(-1)
    return v, t

def write(parts, out_prefix):
    """parts: dict name -> shape. Writes <prefix>.bin and <prefix>.json."""
    man, off, chunks = [], 0, []
    for name, shp in parts.items():
        try:
            v, t = tess(shp)
        except Exception as e:  # noqa: BLE001
            print("tessellate failed", name, e); continue
        if len(t) == 0: continue
        vb, tb = v.tobytes(), t.tobytes()
        man.append({"name": name, "voff": off, "vn": len(v), "toff": off + len(vb), "tn": len(t)})
        chunks += [vb, tb]; off += len(vb) + len(tb)
    with open(out_prefix + ".bin", "wb") as f:
        for c in chunks: f.write(c)
    json.dump(man, open(out_prefix + ".json", "w"))
    return man

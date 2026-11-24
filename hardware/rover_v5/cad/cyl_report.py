import sys, os, math, collections
sys.path.insert(0, os.path.dirname(__file__))
from build123d import import_step, GeomType
from load_step import leaves
want = sys.argv[2].split(",")
root = import_step(sys.argv[1])
for path, s in leaves(root):
    if path[-1] not in want: continue
    print("==", path[-1])
    seen = collections.OrderedDict()
    for f in s.faces():
        if f.geom_type != GeomType.CYLINDER: continue
        a = f.axis_of_rotation; d = a.direction; p = a.position; fb = f.bounding_box()
        ax = "x" if abs(d.X) > .99 else "y" if abs(d.Y) > .99 else "z" if abs(d.Z) > .99 else f"({d.X:.2f},{d.Y:.2f},{d.Z:.2f})"
        if ax == "x": key = (ax, round(2*f.radius,1), None, round(p.Y,1), round(p.Z,1))
        elif ax == "y": key = (ax, round(2*f.radius,1), round(p.X,1), None, round(p.Z,1))
        elif ax == "z": key = (ax, round(2*f.radius,1), round(p.X,1), round(p.Y,1), None)
        else: key = (ax, round(2*f.radius,1), round(p.X,1), round(p.Y,1), round(p.Z,1))
        lo = {"x": fb.min.X, "y": fb.min.Y, "z": fb.min.Z}.get(ax, 0); hi = {"x": fb.max.X, "y": fb.max.Y, "z": fb.max.Z}.get(ax, 0)
        if key in seen: seen[key] = (min(seen[key][0], lo), max(seen[key][1], hi))
        else: seen[key] = (lo, hi)
    for k, (lo, hi) in seen.items():
        if k[1] > 60: continue
        print(f"  axis {k[0]} dia {k[1]:6.1f} at x={k[2]} y={k[3]} z={k[4]}  span {lo:.1f}..{hi:.1f}")

"""Free 3D models placed in the world as shared instances.

An asset is one real model (a bench, a street light) stored once and drawn at
many places. `ASSETS` lists where each one comes from and its licence;
`fetch_assets` downloads and prepares them into a cache folder; the world
builder then places instances wherever the map data has that kind of object.
When an asset is not in the cache the builder falls back to its simple
stand-in shape, so builds never depend on the network.

Only models whose licence allows this use are listed. Do not add Mixamo
characters (their terms forbid machine-learning use) or anything taken from
Google Maps.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# name -> where it comes from. `cls` is the semantic label of every instance.
ASSETS = {
    "bench": {"source": "polyhaven", "id": "painted_wooden_bench", "cls": "bench"},
    "street_light": {"source": "polyhaven", "id": "street_lamp_01", "cls": "street_light",
                     "max_tris": 800},
}
# Kenney's Car Kit (CC0): low-poly, toy-proportioned cars. They face +Z and are about
# 2.6 units long, so they are turned to face +X and enlarged to roughly car size.
for _car in ("sedan", "hatchback-sports", "suv", "van", "truck", "delivery"):
    ASSETS[f"car_{_car.replace('-', '_')}"] = {
        "source": "kenney", "kit": "car-kit", "file": f"Models/GLB format/{_car}.glb",
        "cls": "car", "scale": 1.3, "turn_deg": 90.0}


@dataclass
class Primitive:
    verts: np.ndarray                 # (n, 3) meters, Z up, base at z = 0
    faces: np.ndarray                 # (m, 3)
    uv: np.ndarray | None             # (n, 2), origin bottom-left
    color: tuple[float, float, float]
    image: bytes | None = None        # JPEG, or PNG when `alpha`
    alpha: bool = False               # the image's alpha channel cuts the shape out (leaves)


@dataclass
class Asset:
    name: str
    cls: str
    prims: list[Primitive] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def triangles(self) -> int:
        return sum(len(p.faces) for p in self.prims)

    @property
    def size(self) -> np.ndarray:
        pts = np.vstack([p.verts for p in self.prims])
        return pts.max(axis=0) - pts.min(axis=0)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        arrays = {}
        for i, p in enumerate(self.prims):
            arrays[f"v{i}"] = p.verts.astype(np.float32)
            arrays[f"f{i}"] = p.faces.astype(np.int32)
            arrays[f"c{i}"] = np.asarray(p.color, dtype=np.float32)
            arrays[f"a{i}"] = np.asarray(p.alpha)
            if p.uv is not None:
                arrays[f"uv{i}"] = p.uv.astype(np.float32)
            if p.image is not None:
                arrays[f"img{i}"] = np.frombuffer(p.image, dtype=np.uint8)
        np.savez_compressed(folder / f"{self.name}.npz", **arrays)
        (folder / f"{self.name}.json").write_text(json.dumps(
            {"name": self.name, "cls": self.cls, "prims": len(self.prims), **self.meta},
            indent=1))

    @classmethod
    def load(cls, folder: Path, name: str) -> Asset | None:
        if not (folder / f"{name}.npz").exists() or not (folder / f"{name}.json").exists():
            return None
        meta = json.loads((folder / f"{name}.json").read_text())
        data = np.load(folder / f"{name}.npz")
        prims = []
        for i in range(int(meta["prims"])):
            prims.append(Primitive(
                data[f"v{i}"].astype(float), data[f"f{i}"].astype(np.int64),
                data[f"uv{i}"].astype(float) if f"uv{i}" in data else None,
                tuple(float(v) for v in data[f"c{i}"]),
                data[f"img{i}"].tobytes() if f"img{i}" in data else None,
                bool(data[f"a{i}"]) if f"a{i}" in data else False))
        return cls(name, meta["cls"], prims, {k: v for k, v in meta.items()
                                              if k not in ("name", "cls", "prims")})


@dataclass
class Instance:
    asset: str
    group: str
    x: float
    y: float
    z: float
    yaw: float = 0.0
    scale: float | tuple = 1.0       # one number, or (sx, sy, sz) for a model stretched to fit


def of_class(library: dict, cls: str) -> list:
    """Every loaded model with that semantic class, in a stable order."""
    return [library[name] for name in sorted(library) if library[name].cls == cls]


def load_assets(folder: Path | None) -> dict[str, Asset]:
    out = {}
    if folder is not None and folder.is_dir():
        for name in ASSETS:
            asset = Asset.load(folder, name)
            if asset is not None:
                out[name] = asset
    return out


def from_trimesh(name: str, cls: str, meshes: list, max_tris: int | None = None,
                 texture_px: int = 512, meta: dict | None = None, scale: float = 1.0,
                 turn_deg: float = 0.0) -> Asset:
    """Prepare Y-up trimesh meshes: turn Z up, stand on z = 0, shrink textures.

    Over `max_tris` the model is simplified and painted with its texture's
    average colour, since simplifying drops texture coordinates."""
    prims = []
    for mesh in meshes:
        verts = np.asarray(mesh.vertices, dtype=float)
        verts = np.c_[verts[:, 0], -verts[:, 2], verts[:, 1]]
        faces = np.asarray(mesh.faces)
        uv, color, image = None, (0.6, 0.6, 0.6), None
        material = getattr(mesh.visual, "material", None)
        tex = getattr(material, "baseColorTexture", None)
        factor = getattr(material, "baseColorFactor", None)
        if factor is not None:
            f = np.asarray(factor, dtype=float)
            color = tuple((f[:3] / (255.0 if f.max() > 1 else 1.0)).tolist())
        if tex is not None:
            rgb = tex.convert("RGB")
            if max_tris and len(faces) > max_tris:
                color = tuple((np.asarray(rgb.resize((32, 32)), dtype=float).reshape(-1, 3)
                               .mean(axis=0) / 255).tolist())
            else:
                rgb.thumbnail((texture_px, texture_px))
                buf = io.BytesIO()
                rgb.save(buf, "JPEG", quality=85)
                image, uv, color = buf.getvalue(), np.asarray(mesh.visual.uv, dtype=float), \
                    (1.0, 1.0, 1.0)
        if max_tris and len(faces) > max_tris:
            import trimesh

            slim = trimesh.Trimesh(verts, faces, process=False).simplify_quadric_decimation(
                face_count=max_tris)
            verts, faces, uv = np.asarray(slim.vertices), np.asarray(slim.faces), None
        prims.append(Primitive(verts, faces, uv, color, image))
    base = min(p.verts[:, 2].min() for p in prims)
    c, s = np.cos(np.radians(turn_deg)), np.sin(np.radians(turn_deg))
    for p in prims:
        p.verts[:, 2] -= base
        x, y = p.verts[:, 0].copy(), p.verts[:, 1].copy()
        p.verts[:, 0], p.verts[:, 1] = x * c - y * s, x * s + y * c     # face +X
        p.verts *= scale
    return Asset(name, cls, prims, meta or {})


def fetch_assets(folder: Path, force: bool = False, progress=print) -> dict:  # pragma: no cover
    """Download every asset in ASSETS and prepare it into `folder`."""
    import tempfile
    import time
    import urllib.request

    import trimesh

    def get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "campus-rover/0.1"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    return resp.read()
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(2 + 2 * attempt)
        raise RuntimeError("unreachable")

    def kenney_kit(slug: str) -> Path:
        """Download one free Kenney kit the way its own page does: read the page, ask
        for the signed link with that page as referrer, then fetch the zip."""
        import http.cookiejar
        import re
        import zipfile

        target = Path(tempfile.mkdtemp()) / slug
        agent = {"User-Agent": "Mozilla/5.0 campus-rover/0.1 (student research)"}
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page = f"https://kenney.nl/assets/{slug}"
        html = opener.open(urllib.request.Request(page, headers=agent), timeout=60).read()
        html = html.decode("utf8", "ignore")
        if "CC0" not in html:
            raise RuntimeError(f"{page} no longer says CC0; check the licence")
        ident = re.search(r"data-download-id=['\"]?([A-Za-z0-9_-]+)", html).group(1)
        link = json.load(opener.open(urllib.request.Request(
            f"https://kenney.nl/download-link/{ident}", headers={**agent, "Referer": page}),
            timeout=60))["url"]
        data = opener.open(urllib.request.Request(link, headers={**agent, "Referer": page}),
                           timeout=300).read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for member in z.namelist():
                if member.startswith("Models/GLB format/"):
                    z.extract(member, target)
        return target

    done, kits = {}, {}
    for name, spec in ASSETS.items():
        if (folder / f"{name}.npz").exists() and not force:
            continue
        if spec["source"] == "kenney":
            if spec["kit"] not in kits:
                kits[spec["kit"]] = kenney_kit(spec["kit"])
            scene = trimesh.load(kits[spec["kit"]] / spec["file"])
            asset = from_trimesh(name, spec["cls"], list(scene.dump(concatenate=False)),
                                 scale=spec.get("scale", 1.0),
                                 turn_deg=spec.get("turn_deg", 0.0), meta={
                "source": f"https://kenney.nl/assets/{spec['kit']}", "licence": "CC0",
                "authors": ["Kenney"], "note": "low-poly, toy proportions"})
            asset.save(folder)
            done[name] = asset.triangles
            progress(f"{name:<22}{asset.triangles:>6} triangles, "
                     f"{'x'.join(f'{v:.2f}' for v in asset.size)} m  <- Kenney {spec['kit']} (CC0)")
            continue
        pid = spec["id"]
        info = json.loads(get(f"https://api.polyhaven.com/info/{pid}"))
        files = json.loads(get(f"https://api.polyhaven.com/files/{pid}"))["gltf"]["1k"]["gltf"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "textures").mkdir()
            (root / f"{pid}.gltf").write_bytes(get(files["url"]))
            for rel, inc in files["include"].items():
                (root / rel).write_bytes(get(inc["url"]))
            scene = trimesh.load(root / f"{pid}.gltf")
            meshes = scene.dump(concatenate=False) if hasattr(scene, "dump") else [scene]
            asset = from_trimesh(name, spec["cls"], list(meshes), spec.get("max_tris"), meta={
                "source": f"https://polyhaven.com/a/{pid}", "licence": "CC0",
                "authors": sorted((info.get("authors") or {}).keys())})
        asset.save(folder)
        done[name] = asset.triangles
        progress(f"{name:<14}{asset.triangles:>7} triangles, "
                 f"{'x'.join(f'{v:.2f}' for v in asset.size)} m  <- {pid} (CC0)")
    return done

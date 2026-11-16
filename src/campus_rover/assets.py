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


@dataclass
class Primitive:
    verts: np.ndarray                 # (n, 3) meters, Z up, base at z = 0
    faces: np.ndarray                 # (m, 3)
    uv: np.ndarray | None             # (n, 2), origin bottom-left
    color: tuple[float, float, float]
    image: bytes | None = None        # JPEG


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
                data[f"img{i}"].tobytes() if f"img{i}" in data else None))
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
    scale: float = 1.0


def load_assets(folder: Path | None) -> dict[str, Asset]:
    out = {}
    if folder is not None and folder.is_dir():
        for name in ASSETS:
            asset = Asset.load(folder, name)
            if asset is not None:
                out[name] = asset
    return out


def from_trimesh(name: str, cls: str, meshes: list, max_tris: int | None = None,
                 texture_px: int = 512, meta: dict | None = None) -> Asset:
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
    for p in prims:
        p.verts[:, 2] -= base
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

    done = {}
    for name, spec in ASSETS.items():
        if (folder / f"{name}.npz").exists() and not force:
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

"""Street furniture drawn from what UH's photos of the route show.

- The lamp posts at Architecture and Holmes Hall are slim dark-bronze poles with a flat drum
  shade (UH photos 1016_03 and 1016_04); the Campus Center plaza adds white globes (1055_03).
- The plaza's trash cans are white drums with slots (1055_03).

Positions come from UH's surveyed layers; these shapes are read from the photos, so they are a
fair likeness, not a measurement.
"""

from __future__ import annotations

import math

import numpy as np

from .assets import Asset, Primitive


def _tube(radius0, radius1, z0, z1, seg=8):
    ang = np.linspace(0, 2 * math.pi, seg, endpoint=False)
    ring0 = np.c_[radius0 * np.cos(ang), radius0 * np.sin(ang), np.full(seg, z0)]
    ring1 = np.c_[radius1 * np.cos(ang), radius1 * np.sin(ang), np.full(seg, z1)]
    i = np.arange(seg)
    j = (i + 1) % seg
    faces = np.r_[np.c_[i, j, j + seg], np.c_[i, j + seg, i + seg]]
    verts = np.vstack([ring0, ring1, [[0, 0, z1]]])
    faces = np.r_[faces, np.c_[i + seg, j + seg, np.full(seg, 2 * seg)]]
    return verts, faces


def _join(parts):
    verts, faces, n = [], [], 0
    for v, f in parts:
        verts.append(v)
        faces.append(f + n)
        n += len(v)
    return np.vstack(verts), np.vstack(faces)


def drum_lamp(height: float = 5.0) -> Asset:
    """A pole with a flat drum shade, 5 m tall (scale it to the surveyed height)."""
    pole = _join([_tube(0.09, 0.09, 0.0, 0.25), _tube(0.065, 0.045, 0.25, height - 0.25)])
    shade_v, shade_f = _join([_tube(0.38, 0.36, height - 0.3, height - 0.08, 12)])
    glow_v, glow_f = _tube(0.33, 0.33, height - 0.305, height - 0.30, 12)
    prims = [Primitive(pole[0], pole[1], None, (0.13, 0.11, 0.10)),
             Primitive(shade_v, shade_f, None, (0.10, 0.09, 0.09)),
             Primitive(glow_v, glow_f, None, (0.95, 0.93, 0.80))]
    return Asset("lamp_drum", "street_light", prims, {"source": "drawn for this project",
                                                      "licence": "own work",
                                                      "from": "UH photos 1016_03, 1016_04"})


def slotted_trash_can() -> Asset:
    """A white drum with dark slots, 0.95 m tall (UH photo 1055_03)."""
    body = _tube(0.30, 0.30, 0.0, 0.95, 12)
    cap = _tube(0.31, 0.31, 0.95, 1.0, 12)
    slots = []
    for k in range(6):
        a = 2 * math.pi * k / 6
        for z in (0.35, 0.55, 0.75):
            c, s = math.cos(a), math.sin(a)
            slots.append((np.array([[0.305 * c - 0.04 * s, 0.305 * s + 0.04 * c, z],
                                    [0.305 * c + 0.04 * s, 0.305 * s - 0.04 * c, z],
                                    [0.305 * c + 0.04 * s, 0.305 * s - 0.04 * c, z + 0.12],
                                    [0.305 * c - 0.04 * s, 0.305 * s + 0.04 * c, z + 0.12]]),
                          np.array([[0, 1, 2], [0, 2, 3]])))
    sv, sf = _join(slots)
    white = _join([body, cap])
    return Asset("trash_can_slotted", "trash_can",
                 [Primitive(white[0], white[1], None, (0.86, 0.87, 0.86)),
                  Primitive(sv, sf, None, (0.15, 0.16, 0.16))],
                 {"source": "drawn for this project", "licence": "own work",
                  "from": "UH photo 1055_03"})

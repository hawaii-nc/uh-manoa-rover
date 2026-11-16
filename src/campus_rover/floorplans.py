"""Floor counts from a campus's published floor-plan PDFs.

UH Mānoa's planning office publishes one "Space Inventory Drawing" PDF per
building, named by building number, with one sheet per level. Each sheet
carries a code such as `1163-100E` (building 1163, level 100, east wing) or
`1163-000W` (basement, west wing). Counting the distinct levels gives the
number of floors without estimating it from the building's height.

The PDFs are for information only and UH asks that they are not passed on, so
they stay in the site's cache; only the floor counts are kept in the repo.
"""

from __future__ import annotations

import re

_LEVEL = re.compile(r"^(\d{1,2})00(M?)[A-Z]{0,3}$")


def levels_from_codes(codes: list[str]) -> dict:
    """Sheet codes (the part after the building number) -> floors summary.

    '100', '200E', '200W' -> 2 floors. '000' is a basement and 'M' marks a
    mezzanine; neither counts as a floor above ground."""
    floors, basement, mezzanine = set(), False, False
    for code in codes:
        m = _LEVEL.match(code.strip().upper())
        if not m:
            continue
        level = int(m.group(1))
        if m.group(2):
            mezzanine = True
        elif level == 0:
            basement = True
        else:
            floors.add(level)
    return {"floors": len(floors), "top_level": max(floors, default=0), "basement": basement,
            "mezzanine": mezzanine}


def sheet_codes(text_pages: list[str], building_number: str) -> list[str]:
    """Find the sheet codes for one building in the text of its PDF pages."""
    pattern = re.compile(rf"\b{re.escape(building_number)}-([A-Z0-9]+)\b")
    found: list[str] = []
    for text in text_pages:
        found.extend(pattern.findall(text or ""))
    return sorted(set(found))


def fetch_floor_counts(numbers: list[str], folder, url: str,
                       progress=print) -> dict:  # pragma: no cover - network
    """Download each building's PDF (if not cached) and count its levels.

    `url` has `{number}` where the building number goes."""
    import time
    import urllib.error
    import urllib.request

    import pypdf

    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for number in numbers:
        path = folder / f"{number}.pdf"
        if not path.exists():
            req = urllib.request.Request(url.format(number=number),
                                         headers={"User-Agent": "campus-rover/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = resp.read()
            except (urllib.error.HTTPError, OSError):
                continue
            if not data.startswith(b"%PDF"):
                continue
            path.write_bytes(data)
            time.sleep(0.4)             # be polite to the campus server
        try:
            pages = [p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages]
        except Exception:
            continue
        summary = levels_from_codes(sheet_codes(pages, number))
        if summary["floors"]:
            out[number] = {**summary, "source": url.format(number=number)}
    progress(f"floor plans: {len(out)} of {len(numbers)} buildings")
    return out

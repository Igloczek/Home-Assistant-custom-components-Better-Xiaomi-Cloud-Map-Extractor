"""Synthetic Roidmi cloud maps, built without the code under test.

The cloud object is gzip of `header + bitmap + map info JSON`. The bitmap has one byte per cell,
row 0 at the bottom: 0 wall, 127 outside, 255 unknown, any other value a room id from `autoArea`.

- **v60 and the other Roidmi models** (what vacuum-map-parser-roidmi reads): a 16-byte header, and
  the bitmap's last cell is outside (127), which the parser takes as the end of the bitmap.
- **sdj60** (upstream #572, PR #768): the map from `get_interim_file_url_pro`. Nothing marks the end
  of the bitmap: it is the `width * height` bytes right before the JSON. The tests use a header
  other than 16 bytes and a last cell that is not 127, which is what broke the v60 split.

No map from a real vacuum is public; the layout follows PR #768 and the parser.
"""

from __future__ import annotations

import gzip
import json

WIDTH = 6
HEIGHT = 5
OUTSIDE, WALL, UNKNOWN = 127, 0, 255
KITCHEN, HALL = 10, 11
RESOLUTION = 0.05


def bitmap(last_cell: int = OUTSIDE) -> bytes:
    """Kitchen in the bottom-left 2 x 2 cells, Hall in the 2 x 3 cells right of it, walls around."""
    rows = [
        [KITCHEN, KITCHEN, HALL, HALL, WALL, OUTSIDE],
        [KITCHEN, KITCHEN, HALL, HALL, WALL, OUTSIDE],
        [WALL, WALL, HALL, HALL, WALL, OUTSIDE],
        [WALL, WALL, WALL, WALL, WALL, UNKNOWN],
        [OUTSIDE, OUTSIDE, OUTSIDE, OUTSIDE, OUTSIDE, last_cell],
    ]
    return bytes(cell for row in rows for cell in row)


def map_info(version: int = 0) -> dict:
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "resolution": RESOLUTION,
        "x_min": 0.0,
        "y_min": 0.0,
        "autoArea": [{"id": KITCHEN, "name": "Kitchen"}, {"id": HALL, "name": "Hall"}],
        "chargeHandlePos": [100, 150],
        "chargeHandlePhi": 0,
        "robotPos": [125, 125],
        "robotPhi": 0,
        "posArray": json.dumps([[100, 100], [150, 100], [150, 150]]),
        "version": version,
    }


def roidmi_map(version: int = 0) -> bytes:
    """A v60-style map, as vacuum-map-parser-roidmi expects it."""
    return gzip.compress(b"\x01" * 16 + bitmap(OUTSIDE) + json.dumps(map_info(version)).encode())


def sdj60_map(version: int = 0, header: bytes = b"\x01" * 20, last_cell: int = WALL) -> bytes:
    """An sdj60 map from `get_interim_file_url_pro`."""
    return gzip.compress(header + bitmap(last_cell) + json.dumps(map_info(version)).encode())


# What `get_interim_file_url` points at for an sdj60 (the app no longer reads it): a JSON list that
# starts with a timestamp, as posted in upstream #572. The v2 parser failed on it with "list indices
# must be integers or slices, not str".
SDJ60_INTERIM_FILE = gzip.compress(json.dumps([1741109371, 0, 3700, -1110, 0, 0, 52, 12, 392]).encode())

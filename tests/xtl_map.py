"""Synthetic Jonr P20 (`xtl.vacuum.xm2216`) cloud maps, built without the code under test.

The layout is the one documented in upstream #621 and Maedrawd's fork
(github.com/Maedrawd/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor-jonrp20, MIT),
which three owners confirmed against the Mi Home app:

- the cloud object is plain JSON: `{"mapId", "fields": [10 entries], "cleanValues"}`;
- `fields[0]` is base64 of a JSON object: the canvas (`totalWidth`, `totalHeight`), the map window
  (`width`, `height`, `xMin`, `yMax`), `resolution`, `chargePos` (a JSON string, heading in
  centi-degrees), and `map`: base64 of an LZ4 *block* of `lz4Len` bytes, one byte per cell, grid row 0
  first: 0 unexplored, 1 wall, 2 unidentified floor, 3 and up a room id;
- `fields[1]` is base64 of `{"traceId", "start", "totalCount", "trace", "lz4Len"}`, `trace` being
  base64 of an LZ4 block of 7-byte records (x int16 LE, y int16 LE, heading int16 LE in
  centi-degrees, flags uint8);
- `fields[2]` is the live position, a JSON string `{"x", "y", "a", "i"}`.

Positions are in cells: device x is the image column (x - yMax), device y the row from the top
(y - (totalHeight - xMin - height)). No map from a real vacuum is public, so these values are made up.
"""

from __future__ import annotations

import base64
import json
import struct

WIDTH = 40
HEIGHT = 30
TOTAL = 800
X_MIN = 300
Y_MAX = 380
# Device y of the image's top row.
Y_TOP = TOTAL - X_MIN - HEIGHT
UNEXPLORED, WALL, FLOOR = 0, 1, 2
LIVING, KITCHEN = 3, 11  # room ids are neither contiguous nor capped (a real capture used 3..9 and 11)

CHARGER = {"x": Y_MAX + 10, "y": Y_TOP + 10, "a": 18000}
VACUUM = {"x": Y_MAX + 20, "y": Y_TOP + 15, "a": 9000, "i": 0}


def to_pixel(x: int, y: int) -> tuple[int, int]:
    """Where a device position lands in the picture (scale 1, no trim)."""
    return x - Y_MAX, y - Y_TOP


def grid() -> list[list[int]]:
    """Rows from grid row 0 (drawn at the bottom). Living room on the left, kitchen on the right,
    a strip of unidentified floor in the living room, unexplored above row 25."""
    rows = []
    for row in range(HEIGHT):
        if row >= 26:
            rows.append([UNEXPLORED] * WIDTH)
        elif row in (0, 25):
            rows.append([WALL] * WIDTH)
        else:
            cells = [WALL] + [LIVING] * 19 + [WALL] + [KITCHEN] * 18 + [WALL]
            if row == 5:
                cells[2:8] = [FLOOR] * 6
            rows.append(cells)
    return rows


def lz4_block(data: bytes) -> bytes:
    """An LZ4 block with runs of a byte as offset-1 matches, so the decoder's overlapping copy is
    exercised. The last 12 bytes are literals, as the LZ4 block format requires."""
    out = bytearray()

    def length(extra: int) -> None:
        while extra >= 255:
            out.append(255)
            extra -= 255
        out.append(extra)

    def sequence(literals: bytes, match: int | None) -> None:
        lit = len(literals)
        token = min(lit, 15) << 4
        if match is not None:
            token |= min(match - 4, 15)
        out.append(token)
        if lit >= 15:
            length(lit - 15)
        out.extend(literals)
        if match is not None:
            out.extend(struct.pack("<H", 1))
            if match - 4 >= 15:
                length(match - 4 - 15)

    start = i = 0
    end = len(data) - 12
    while i < end:
        run = 1
        while i + run < end and data[i + run] == data[i]:
            run += 1
        if run >= 5:
            sequence(data[start:i + 1], run - 1)
            i += run
            start = i
        else:
            i += run
    sequence(data[start:], None)
    return bytes(out)


def _b64json(value: object) -> str:
    return base64.b64encode(json.dumps(value).encode()).decode()


def trace_field(points: list[tuple[int, int, int]], flags: int = 128) -> str:
    body = b"".join(struct.pack("<hhhB", x, y, a, flags) for x, y, a in points)
    return _b64json({
        "traceId": 1, "start": 1, "totalCount": len(points),
        "trace": base64.b64encode(lz4_block(body)).decode(), "lz4Len": len(body),
    })


# From the charger to the vacuum, no step over 5 cells (a longer one starts a new segment).
TRACE = [
    (CHARGER["x"], CHARGER["y"], 0), (CHARGER["x"] + 3, CHARGER["y"], 0), (CHARGER["x"] + 6, CHARGER["y"] + 2, 4500),
    (VACUUM["x"] - 1, VACUUM["y"] - 1, 9000), (VACUUM["x"], VACUUM["y"], 9000),
]


def xtl_map(version: int = 0, *, cells: list[list[int]] | None = None, trace: str | None = None,
            position: dict | None = None) -> bytes:
    raw_grid = bytes(cell for row in (cells or grid()) for cell in row)
    field0 = {
        "expanded": 0, "totalWidth": TOTAL, "totalHeight": TOTAL,
        "width": WIDTH, "height": HEIGHT, "xMin": X_MIN, "yMax": Y_MAX, "resolution": 5,
        "timestamp": str(version),
        "map": base64.b64encode(lz4_block(raw_grid)).decode(), "lz4Len": len(raw_grid),
        "direction": 1, "chargePos": json.dumps(CHARGER),
    }
    live = dict(position or VACUUM)
    live["x"] += version  # a different map for every version
    fields = [_b64json(field0), trace if trace is not None else trace_field(TRACE), json.dumps(live),
              "", "", "", "", "", "", []]
    return json.dumps({"mapId": version, "fields": fields, "cleanValues": []}).encode()

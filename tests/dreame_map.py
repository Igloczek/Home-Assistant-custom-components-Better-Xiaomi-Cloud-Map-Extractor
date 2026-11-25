"""Dreame maps built the way the vacuums upload them, for the real `vacuum-map-parser-dreame`.

A map is an I-frame: a 27-byte header, one byte per pixel and a JSON object, zlib-compressed and
base64-encoded. The unencrypted models (p2008 and other `map_view` models) use urlsafe base64; the
encrypted ones AES-CBC encrypt the zlib stream first, with a key derived from the one that comes
with the map name and an IV that is fixed per model (`DreameMapDataParser.IVs`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

FIXTURES = Path(__file__).parent / "fixtures" / "dreame"
# Upstream #450: what a dreame.vacuum.md1808 serves from the cloud, rebuilt from a made-up map with a
# made-up key and IV (see fixtures/dreame/README.md).
MD1808_ENCRYPTED_MAP = FIXTURES / "md1808_encrypted_map.b64.txt"

I_FRAME = 73


def build_frame(map_id: int = 0, width: int = 2, height: int = 2) -> bytes:
    """An I-frame of `width` x `height` empty pixels; `map_id` makes a different map."""
    header = struct.pack(
        "<hhB3h3h5h",
        map_id, 0, I_FRAME,
        0, 0, 0,  # robot x, y, angle
        0, 0, 0,  # charger x, y, angle
        50, width, height, 0, 0,  # pixel size, width, height, left, top
    )
    return header + bytes(width * height) + json.dumps({}).encode()


def plain_map(frame: bytes) -> bytes:
    """Unencrypted upload: urlsafe base64 of the zlib stream."""
    return base64.urlsafe_b64encode(zlib.compress(frame))


def aes_key(map_key: str) -> bytes:
    return hashlib.sha256(map_key.encode()).hexdigest()[:32].encode()


def encrypted_map(frame: bytes, map_key: str, iv: str) -> bytes:
    """Encrypted upload: standard base64 of the AES-CBC encrypted zlib stream."""
    cipher = AES.new(aes_key(map_key), AES.MODE_CBC, iv=iv.encode())
    return base64.b64encode(cipher.encrypt(pad(zlib.compress(frame), AES.block_size)))

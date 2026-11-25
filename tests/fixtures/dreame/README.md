# Dreame fixtures

- `md1808_encrypted_map.b64.txt`: the shape of the raw map a dreame.vacuum.md1808 (Xiaomi Robot
  Vacuum-Mop 2C) serves from Xiaomi Cloud, after upstream #450 (also #757, #772). It is synthetic,
  not the attached map. The attachment was used only to learn the format: one line of
  standard base64 (with `+` and `/`, no newline) decoding to 5136 bytes, a multiple of the AES block
  size, about 7.96 bits of entropy per byte, with no zlib or gzip header. No raw or windowed deflate
  stream starts in it, and none of the known Dreame IVs or keys (vacuum-map-parser-dreame 0.1.3,
  Tasshack/dreame-vacuum) opens it as a key, a derived key or an IV. That leaves the AES-CBC
  scheme of the encrypted Dreame models: a per-map key that comes with the map name, and a
  per-model IV that is not published for md1808.

  This file is a 40 x 30 empty I-frame (`tests/dreame_map.py`, `build_frame(width=40, height=30)`)
  encrypted the same way, with the made-up key `made-up-md1808-map-key` and the made-up IV
  `MadeUpMd1808IV00`:

  ```python
  encrypted_map(build_frame(width=40, height=30), "made-up-md1808-map-key", "MadeUpMd1808IV00")
  ```

Unencrypted Dreame maps (p2008 and the other `map_view` models) are built in the tests with
`plain_map` from the same module.

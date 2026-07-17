#!/usr/bin/env python3
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
# Personal use only — see browser-extension copyright headers for full terms.
"""
Generates simple solid-colour PNG icons for the browser extension.
Run once from the browser-extension directory:
    python generate-icons.py
"""

import os
import struct
import zlib


def _png(size: int, r: int, g: int, b: int) -> bytes:
    """Builds a minimal solid-colour RGB PNG of the given square size."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    # Each scanline: filter byte 0 followed by (r, g, b) * width
    row = b"\x00" + bytes([r, g, b]) * size
    idat = chunk(b"IDAT", zlib.compress(row * size, 9))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def main() -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "icons")
    os.makedirs(out_dir, exist_ok=True)

    # Blue (#1e40af) matching the popup header colour
    r, g, b = 0x1E, 0x40, 0xAF

    for size in (16, 48, 128):
        path = os.path.join(out_dir, f"icon{size}.png")
        with open(path, "wb") as fh:
            fh.write(_png(size, r, g, b))
        print(f"  Created {path}")

    print(
        "Done. Icons are solid blue (#1e40af) — replace with your own artwork if desired."
    )


if __name__ == "__main__":
    main()

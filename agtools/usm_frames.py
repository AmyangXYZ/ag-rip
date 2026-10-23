#!/usr/bin/env python3
r"""Frames from one of the game's CRI USM videos - in-game footage to check a scene against.

    python agtools/usm_frames.py SofdecAsset/function/dlc/119401_login.usm out_dir [--every 2.0]

The game's USMs (StreamingAssets/Windows_restored/SofdecAsset/...) are VP9 in IVF
chunks (@SFV), masked with CRI Mana's video XOR under the key every client ships
(CriWareInitializer.DecrypterConfig.key). This demuxes the video stream, unmasks each
frame, writes <name>.ivf and pulls a PNG every --every seconds with the ffmpeg that
imageio-ffmpeg bundles. The login videos film the DLC scene with their own cameras:
good for lighting, dressing and staging, not for frame matching.
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys

import numpy as np

KEY = 1578660190369042886
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "AetherGazer", "AetherGazer_Data", "StreamingAssets", "Windows_restored")


def video_masks(key: int) -> tuple[np.ndarray, np.ndarray]:
    """CRI Mana's two 32-byte video masks from the 64-bit key."""
    k1, k2 = key & 0xFFFFFFFF, key >> 32
    t = [0] * 0x20
    t[0x00] = k1 & 0xFF
    t[0x01] = (k1 >> 8) & 0xFF
    t[0x02] = (k1 >> 16) & 0xFF
    t[0x03] = ((k1 >> 24) & 0xFF) - 0x34
    t[0x04] = (k2 & 0xFF) + 0xF9
    t[0x05] = ((k2 >> 8) & 0xFF) ^ 0x13
    t[0x06] = ((k2 >> 16) & 0xFF) + 0x61
    t[0x07] = t[0x00] ^ 0xFF
    t[0x08] = t[0x02] + t[0x01]
    t[0x09] = t[0x01] - t[0x07]
    t[0x0A] = t[0x02] ^ 0xFF
    t[0x0B] = t[0x01] ^ 0xFF
    t[0x0C] = t[0x0B] + t[0x09]
    t[0x0D] = t[0x08] - t[0x03]
    t[0x0E] = t[0x0D] ^ 0xFF
    t[0x0F] = t[0x0A] - t[0x0B]
    t[0x10] = t[0x08] - t[0x0F]
    t[0x11] = t[0x10] ^ t[0x07]
    t[0x12] = t[0x0F] ^ 0xFF
    t[0x13] = t[0x03] ^ 0x10
    t[0x14] = t[0x04] - 0x32
    t[0x15] = t[0x05] + 0xED
    t[0x16] = t[0x06] ^ 0xF3
    t[0x17] = t[0x13] - t[0x0F]
    t[0x18] = t[0x15] + t[0x07]
    t[0x19] = 0x21 - t[0x13]
    t[0x1A] = t[0x14] ^ t[0x17]
    t[0x1B] = t[0x16] + t[0x16]
    t[0x1C] = t[0x17] + 0x44
    t[0x1D] = t[0x03] + t[0x04]
    t[0x1E] = t[0x05] - t[0x16]
    t[0x1F] = t[0x1D] ^ t[0x13]
    m1 = np.array([v & 0xFF for v in t], np.uint8)
    return m1, m1 ^ 0xFF


def unmask(frame: bytes, m1: np.ndarray, m2: np.ndarray) -> bytes:
    """Mana's video unmask. Past the 0x40 header and the first 0x100 bytes, each byte
    is XORed with the plain byte 32 before it and mask 2 - a running XOR down each of
    32 lanes; the first 0x100 then take mask 1 folded with the plain bytes after them."""
    d = np.frombuffer(frame, np.uint8).copy()
    size = len(d) - 0x40
    if size < 0x200:
        return frame
    body = d[0x140:0x40 + size]
    n = len(body)
    lanes = np.zeros(((n + 31) // 32) * 32, np.uint8)
    lanes[:n] = body ^ np.resize(m2, n)
    lanes = np.bitwise_xor.accumulate(lanes.reshape(-1, 32), axis=0).reshape(-1)
    d[0x140:0x40 + size] = lanes[:n]
    mask = m1.copy()
    for i in range(0x100):
        mask[i & 0x1F] ^= d[0x140 + i]
        d[0x40 + i] ^= mask[i & 0x1F]
    return d.tobytes()


def video_frames(data: bytes):
    """The @SFV data chunks' payloads, in order."""
    pos = 0
    while pos + 8 <= len(data):
        sig = data[pos:pos + 4]
        size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if sig == b"@SFV":
            offset = data[pos + 9]
            padding = struct.unpack(">H", data[pos + 10:pos + 12])[0]
            kind = data[pos + 15] & 3
            if kind == 0:
                yield data[pos + 8 + offset:pos + 8 + size - padding]
        pos += 8 + size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("usm", help="path under Windows_restored, or a file")
    ap.add_argument("out")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between PNGs")
    a = ap.parse_args()
    src = a.usm if os.path.isfile(a.usm) else os.path.join(ROOT, a.usm)
    os.makedirs(a.out, exist_ok=True)
    m1, m2 = video_masks(KEY)
    name = os.path.splitext(os.path.basename(src))[0]
    ivf = os.path.join(a.out, f"{name}.ivf")
    # the first frame is the IVF file header plus its first frame (DKIF), as the
    # stream was muxed; every frame is unmasked the same way
    with open(src, "rb") as fh, open(ivf, "wb") as out:
        for frame in video_frames(fh.read()):
            out.write(unmask(frame, m1, m2))
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", ivf, "-vf", f"fps=1/{a.every}",
                        os.path.join(a.out, f"{name}_%03d.png")], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:], file=sys.stderr)
        return 1
    print(f"{ivf}; frames every {a.every:g}s in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

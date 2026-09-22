#!/usr/bin/env python3
r"""Put a sequence's placement on a character VMD's master bone, at the camera's scale.

    python agtools/vmd_placement.py <stem.character.vmd> <stem.camera.json> --scale <camera scale>

For a character VMD retargeted onto a model of another height than the game's. The
retarget scales her body to that model (hip heights, reach) - right for the body, wrong
for where the game stands her in the room, which is a WORLD offset and has to move with
the camera. So export her motion character-local (`ag.py cams <skin> --local`), convert
as usual, then key 全ての親 with the placement:

  scale  the camera VMD's own: the "×N" fbx2vmd prints for it. Not measured back from
         the VMD - fbx2vmd slides each shot's eye along its axis to hold a whole-degree
         fov, so a key's eye is not the source's
  axes   fbx2vmd's: Unity -> MMD by a half turn about Y (x and z negate), so a heading
         carries over unchanged

Keys only where the placement changes, stepped at cuts. Any 全ての親 keys the retarget
wrote are replaced.
"""
from __future__ import annotations

import json
import struct
import sys

MASTER = "全ての親"


def place(char_vmd: str, placement: list, scale: float, signs: tuple[int, int, int]) -> int:
    data = open(char_vmd, "rb").read()
    p = 30 + 20
    (n,) = struct.unpack_from("<I", data, p)
    end = p + 4 + n * 111
    recs = [data[p + 4 + k * 111: p + 4 + (k + 1) * 111] for k in range(n)]
    interp = recs[0][47:] if recs else bytes(64)
    name = MASTER.encode("shift_jis").ljust(15, b"\0")
    kept = [r for r in recs if r[:15].rstrip(b"\0") != name.rstrip(b"\0")]
    sx, sy, sz = signs

    def key(frame, pos, q):
        # A rotation through the same axis change S: its axis turns as det(S) * S, so
        # each component takes the product of the other two signs. (-1, 1, -1), a turn
        # about Y, leaves a yaw as it is; a mirror would flip it.
        qx, qy, qz, qw = q
        rq = (qx * sy * sz, qy * sx * sz, qz * sx * sy, qw)
        return (name + struct.pack("<I3f4f", frame, sx * pos[0] * scale, sy * pos[1] * scale, sz * pos[2] * scale,
                                   *rq) + interp)

    frames = []
    for i, f in enumerate(placement):
        if i == 0 or f != placement[i - 1]:
            if i > 0 and (not frames or frames[-1][0] != i - 1):
                frames.append((i - 1, placement[i - 1]))
            frames.append((i, f))
    out = [key(i, f[0], f[1]) for i, f in frames]
    open(char_vmd, "wb").write(data[:p] + struct.pack("<I", len(kept) + len(out)) + b"".join(kept) + b"".join(out)
                               + data[end:])
    return len(out)


AXES = (-1, 1, -1)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("character_vmd")
    ap.add_argument("camera_json")
    ap.add_argument("--scale", type=float, required=True, help="the camera VMD's scale (fbx2vmd's printed xN)")
    a = ap.parse_args()
    char_vmd, camera_json = a.character_vmd, a.camera_json
    placement = json.load(open(camera_json, encoding="utf-8")).get("placement")
    if not placement:
        print("no placement in the JSON; nothing to do")
        return 0
    k = place(char_vmd, placement, a.scale, AXES)
    print(f"{MASTER}: {k} key(s), x{a.scale:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

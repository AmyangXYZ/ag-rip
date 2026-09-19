#!/usr/bin/env python3
r"""Camera sequences -> MMD VMD (motion + morphs, camera) with reze-rig's fbx2vmd.

    python ag.py vmd 109501 --target-pmx D:\reze-rig\public\models\托特\托特.pmx
    python ag.py vmd 109501 --only touch1,debut --target-pmx <model.pmx>

For each <skin>@<seq> in AG_fbx_anim/<cid>/cameras/ (written by `ag.py cams`; a sequence
whose timeline camera is disabled - it stays on the home camera - has no .camera.*):
  <seq>.character.fbx -> <seq>.character.vmd   body retargeted onto the target model (FK, no
                                               foot IK - see --foot-ik; root motion kept),
                                               facial / lip morphs as MMD morphs
  <seq>.camera.fbx    -> <seq>.camera.vmd      the sequence's camera, sized by the character
  <seq>.wav           -> <seq>.mixed.zh.wav     voice + scene music, one track
into AG_dlc_scene/<skin>/ (e.g. AG_dlc_scene/109501/). Everything starts at frame 0.

The converter is reze-rig's scripts/fbx2vmd.ts (https://github.com/AmyangXYZ/reze-rig,
MIT), vendored as a bundle in tools/reze-rig/ - see its README. Needs Node.js.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FBX2VMD = os.path.join(ROOT, "tools", "reze-rig", "fbx2vmd.mjs")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skins", nargs="+", help="skin ids, e.g. 109501 104701")
    ap.add_argument("--target-pmx", required=True, help="the MMD model to retarget onto (its morphs name the VMD's)")
    ap.add_argument("--only", help="comma list of sequences (touch1, debut, win, ...)")
    ap.add_argument("--out", help="output folder (default AG_dlc_scene/<skin>)")
    ap.add_argument("--foot-ik", action="store_true",
                    help="export foot-IK targets (fbx2vmd's default). Off here: with IK on, reze-rig lifts "
                         "the body so no foot sinks below the floor, which cancels falls below floor level "
                         "(e.g. 104701 touch2's dive into the water)")
    args = ap.parse_args()
    if not os.path.isfile(FBX2VMD):
        sys.exit(f"error: {FBX2VMD} missing (see tools/reze-rig/README.md to rebuild it)")
    if shutil.which("node") is None:
        sys.exit("error: Node.js not found")
    only = set(args.only.split(",")) if args.only else None
    for skin in args.skins:
        cid = skin[:4]
        src = os.path.join(ROOT, "AG_fbx_anim", cid, "cameras")
        out = args.out or os.path.join(ROOT, "AG_dlc_scene", skin)
        os.makedirs(out, exist_ok=True)
        for char in sorted(glob.glob(os.path.join(src, f"{skin}@*.character.fbx"))):
            stem = os.path.basename(char)[:-len(".character.fbx")]
            seq = stem.split("@", 1)[1]
            if only and seq not in only:
                continue
            cam = os.path.join(src, f"{stem}.camera.fbx")
            inputs = [p for p in (char, cam) if os.path.isfile(p)]
            stale = os.path.join(out, f"{stem}.camera.vmd")
            if not os.path.isfile(cam) and os.path.isfile(stale):
                os.remove(stale)             # no camera of its own (disabled vcam: home camera)
            r = subprocess.run(["node", FBX2VMD, *inputs, "--out", out, "--target-pmx", args.target_pmx,
                                "--no-bind-ref"] + ([] if args.foot_ik else ["--no-foot-ik"]),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            for line in r.stdout.splitlines():
                if line.endswith(".vmd") or ".vmd  (" in line:
                    print("  " + line)
            if r.returncode:
                print(f"  ! {stem}: fbx2vmd failed\n{r.stderr.strip()[-600:]}")
                continue
            wav = os.path.join(src, f"{stem}.wav")
            if os.path.isfile(wav):
                shutil.copyfile(wav, os.path.join(out, f"{stem}.mixed.zh.wav"))
            print(f"{stem} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

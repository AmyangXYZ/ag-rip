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
  <skin>.scene.json                            the scene's scale, for putting the stage under them
into AG_dlc_scene/<skin>/ (e.g. AG_dlc_scene/109501/). Everything starts at frame 0.

ONE SCALE. Camera, her travel and where the sequence stands her are all in game units
(Unity, timeline root = stage origin) times one factor W = the target model's height /
the heroine's (ankle to head bone): the model stands in for her at her size. The stage
must be scaled by the same W, or everything parts in proportion to its distance from
the origin (128402's wedding spot is 30 units out, 104903's 85). reze-design builds its
stages at 8 PMX units per game unit (0.64 m x 12.5), so its stage scale is W / 8, at
position 0 - both in <skin>.scene.json. Axes agree already: game (x, y, z) -> PMX
(-x, y, -z) on both sides.

The converter is reze-rig's scripts/fbx2vmd.ts (https://github.com/AmyangXYZ/reze-rig,
MIT), vendored as a bundle in tools/reze-rig/ - see its README. Needs Node.js.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FBX2VMD = os.path.join(ROOT, "tools", "reze-rig", "fbx2vmd.mjs")
STAGE_PMX_PER_UNIT = 8.0       # reze-design tools/stages: 1 game unit = 0.64 m, 12.5 PMX per metre


def stage_of(skin: str) -> str | None:
    try:
        cat = json.load(open(os.path.join(ROOT, "AG_stage_names", "catalog.json"), encoding="utf-8"))
        return cat["dlc"].get(skin, {}).get("stage") or None
    except (OSError, KeyError, ValueError):
        return None


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
        scales = set()
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
                m = re.search(r"\(camera, \d+ keys, ×([0-9.]+)", line)
                if m:
                    scales.add(float(m.group(1)))
            if r.returncode:
                print(f"  ! {stem}: fbx2vmd failed\n{r.stderr.strip()[-600:]}")
                continue
            wav = os.path.join(src, f"{stem}.wav")
            if os.path.isfile(wav):
                shutil.copyfile(wav, os.path.join(out, f"{stem}.mixed.zh.wav"))
            print(f"{stem} -> {out}")
        if scales and not only:
            if max(scales) - min(scales) > 1e-3:
                print(f"  ! {skin}: the sequences disagree on the scene scale: {sorted(scales)}")
            w = sorted(scales)[len(scales) // 2]
            scene = {
                "skin": skin,
                "stage": stage_of(skin),
                "pmx_per_game_unit": w,
                "reze_design_stage": {"scale": round(w / STAGE_PMX_PER_UNIT, 4), "position": [0, 0, 0],
                                      "rotation": [0, 0, 0]},
                "target_pmx": os.path.basename(args.target_pmx),
                "note": "camera VMDs, her travel and her placement are game units x pmx_per_game_unit "
                        "(game (x, y, z) -> PMX (-x, y, -z)); a reze-design stage (8 PMX per game unit) "
                        "lines up at reze_design_stage.scale about the origin",
            }
            json.dump(scene, open(os.path.join(out, f"{skin}.scene.json"), "w", encoding="utf-8"), indent=1)
            print(f"{skin}: scene x{w:.3f} PMX per game unit -> stage scale {w / STAGE_PMX_PER_UNIT:.4f}"
                  f" ({skin}.scene.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

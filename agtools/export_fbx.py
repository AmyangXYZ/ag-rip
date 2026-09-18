#!/usr/bin/env python3
"""
Batch-export AetherGazer characters to rigged, textured FBX via AssetStudioModCLI.

Each hero lives in a single bundle (hero/<id>.ys) holding meshes, skeleton, materials
and textures, so one CLI call per character is enough.

What you get per character: several *_tpose.fbx variants - the base model, alternate
costumes (<id>02_tpose), and higher-poly `ui` display models used in menus. Each has
a full Biped armature (150-220 bones), skinned weights and embedded textures.

What you do NOT get: animation. AssetStudioMod does not bind this game's AnimationClips
to the models - every exported FBX comes back with zero actions (verified in Blender
across all 13 outputs for character 1044). The clips are still recoverable as Unity
.anim files via AssetRipper; see extract_character.py.

Usage
-----
  python export_fbx.py 1044 1170        # specific characters
  python export_fbx.py all              # all 132 heroes (~1-2 min each)
  python export_fbx.py all --out D:\\fbx
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

CLI = (r"C:\AetherGazerStarter\tools\AssetStudioModCLI"
       r"\AssetStudioModCLI_net8_portable\AssetStudioModCLI.exe")
HERO = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data\StreamingAssets"
        r"\Windows_restored\comchar\assets\comchar\artresources\char\hero")
UNITY_VERSION = "2022.3.62f3"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+", help="hero IDs, or 'all'")
    ap.add_argument("--out", default=r"C:\AetherGazerStarter\AG_fbx")
    ap.add_argument("--hero-dir", default=HERO)
    ap.add_argument("--cli", default=CLI)
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.cli):
        print(f"error: AssetStudioModCLI not found at {args.cli}", file=sys.stderr)
        return 2

    if len(args.ids) == 1 and args.ids[0].lower() == "all":
        ids = sorted(os.path.splitext(f)[0] for f in os.listdir(args.hero_dir)
                     if f.lower().endswith(".ys"))
    else:
        ids = args.ids

    print(f"{len(ids)} character(s) -> {args.out}")
    ok = failed = skipped = 0
    t_start = time.time()

    for i, cid in enumerate(ids, 1):
        src = os.path.join(args.hero_dir, f"{cid}.ys")
        if not os.path.isfile(src):
            print(f"[{i}/{len(ids)}] {cid}: no bundle, skipping")
            failed += 1
            continue
        out = os.path.join(args.out, cid)
        if args.skip_existing and os.path.isdir(out) and any(
                f.endswith(".fbx") for _, _, fs in os.walk(out) for f in fs):
            skipped += 1
            continue

        t0 = time.time()
        proc = subprocess.run(
            [args.cli, src, "-m", "splitObjects", "--unity-version", UNITY_VERSION,
             "-o", out, "--log-level", "warning", "-r"],
            capture_output=True, text=True)
        n = sum(1 for _, _, fs in os.walk(out) for f in fs if f.endswith(".fbx"))
        if proc.returncode == 0 and n:
            ok += 1
            print(f"[{i}/{len(ids)}] {cid}: {n} fbx  ({time.time()-t0:.0f}s)", flush=True)
        else:
            failed += 1
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            print(f"[{i}/{len(ids)}] {cid}: FAILED rc={proc.returncode} {tail}", flush=True)

    print(f"\ndone: {ok} ok, {failed} failed, {skipped} skipped "
          f"in {(time.time()-t_start)/60:.1f} min -> {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
r"""
Render a picture of each stage, so stages can be matched to DLC previews by eye.

  python agtools/stage_thumbs.py x343 x317        given stages
  python agtools/stage_thumbs.py --prefix x3      every stage whose code starts so

Per stage: dependency closure -> AssetRipper "primary content" export (the scene
as one glb) -> Blender render (render_glb.py) -> AG_stage_names/render/<code>_high.png
and _eye.png. The glb (~300 MB) is deleted straight after. Stages already rendered
are skipped, so the run resumes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assetripper  # noqa: E402
import bundle_deps  # noqa: E402
import scenes  # noqa: E402
import stage_unity  # noqa: E402
from export_anim_fbx import find_blender  # noqa: E402

OUT = r"C:\AetherGazerStarter\AG_stage_names\render"
TMP = r"C:\_agglb"
RENDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_glb.py")


def scene_glb(export_dir: str, code: str) -> str | None:
    d = os.path.join(export_dir, "Assets", "SceneHierarchyObject")
    if not os.path.isdir(d):
        return None
    glbs = [f for f in os.listdir(d) if f.lower().endswith(".glb")]
    exact = [f for f in glbs if os.path.splitext(f)[0].lower() == code.lower()]
    pick = exact or sorted(glbs, key=lambda f: -os.path.getsize(os.path.join(d, f)))
    return os.path.join(d, pick[0]) if pick else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--prefix", help="all stage codes starting with this")
    args = ap.parse_args()
    codes = list(args.codes)
    if args.prefix:
        codes += [c for _, c in scenes.codes() if c.startswith(args.prefix)]
    os.makedirs(OUT, exist_ok=True)
    codes = [c for c in dict.fromkeys(codes)
             if not os.path.isfile(os.path.join(OUT, f"{c}_high.png"))]
    print(f"{len(codes)} stage(s) to render", flush=True)
    blender = find_blender()
    index = bundle_deps.load_index()
    with assetripper.Server(os.path.join(OUT, "_assetripper.log")):
        for i, code in enumerate(codes, 1):
            t0 = time.time()
            try:
                bundles, _ = stage_unity.stage_bundles(code, index)
            except SystemExit as exc:
                print(f"[{i}/{len(codes)}] {code}: {exc}", flush=True)
                continue
            staged, out = os.path.join(TMP, "in"), os.path.join(TMP, "out")
            shutil.rmtree(out, ignore_errors=True)
            stage_unity.link_into(bundles, staged)
            assetripper.post("/Reset", timeout=300)
            assetripper.post("/Settings/Update", assetripper.BASE_SETTINGS, timeout=120)
            assetripper.post("/LoadFolder", {"Path": staged})
            assetripper.post("/Export/PrimaryContent", {"Path": out})
            glb = scene_glb(out, code)
            status = "no scene glb"
            if glb:
                r = subprocess.run([blender, "-b", "--python", RENDER, "--",
                                    glb, os.path.join(OUT, code)],
                                   capture_output=True, text=True, timeout=1800)
                status = "ok" if os.path.isfile(os.path.join(OUT, f"{code}_high.png")) \
                    else "render failed: " + (r.stdout + r.stderr).strip().splitlines()[-1][:120]
            shutil.rmtree(TMP, ignore_errors=True)
            print(f"[{i}/{len(codes)}] {code}: {status} ({time.time() - t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

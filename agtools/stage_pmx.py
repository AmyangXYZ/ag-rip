#!/usr/bin/env python3
r"""An exported stage -> the PMX folder reze.design loads.

    python ag.py pmx x333                   -> AG_pmx/x333-stage/X333.pmx + tex/ + maps/ + X333.hdr + X333.lights.json
    python ag.py pmx x305 x323 x333         several
    python ag.py pmx x333 --scale 12.5      a project whose unit is a metre
    python ag.py pmx x333 --albedo 1024     cap the textures, for a stage too heavy to ship

The second half of `ag.py stage`: that one turns a stage into a Unity project
that renders like the game, this one turns the project into a model. Run it
after the stage command has finished, on the same code.

Textures come out at the game's own resolution — a 2048 albedo stays 2048, and
normal maps with it. The .hdr is the scene's ambient gradient with its reflection
probe ridden on top as structure, and it installs itself onto the World slot when
the stage loads, so the room's fill and its reflections arrive with the geometry.

The converter itself is vendored under tools/unity-stage/ (reze-design's own,
same file) and is runnable on its own with --project/--scene/--out/--name.
Anything after the codes is forwarded to it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage_unity  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONVERTER = os.path.join(ROOT, "tools", "unity-stage", "unity_to_pmx.py")
OUT = r"C:\AetherGazerStarter\AG_pmx"


def scene_for(stage_dir: str, code: str) -> str | None:
    """The one scene this stage is. A project holds exactly one for a level
    export; when it holds several (the prefab-mode spaces, one scene per
    prefab), the code names which — that is how the folder was built."""
    # find_scenes takes the stage folder (it looks under <dir>/ExportedProject/Assets) and
    # returns paths relative to it; the converter wants them relative to the project itself.
    scenes = [os.path.relpath(s, "ExportedProject") for s in stage_unity.find_scenes(stage_dir)]
    if not scenes:
        return None
    if len(scenes) == 1:
        return scenes[0]
    want = code.rsplit("/", 1)[-1].lower()
    named = [s for s in scenes if os.path.splitext(os.path.basename(s))[0].lower() == want]
    return named[0] if named else None


def convert(code: str, extra: list[str]) -> int:
    name = code.rsplit("/", 1)[-1].upper()
    stage_dir = os.path.join(stage_unity.OUT, code.rsplit("/", 1)[-1])
    project = os.path.join(stage_dir, "ExportedProject")
    if not os.path.isdir(project):
        print(f"{code}: no export at {project} - run `python ag.py stage {code}` first", file=sys.stderr)
        return 2
    scene = scene_for(stage_dir, code)
    if not scene:
        print(f"{code}: no .unity scene under {project}", file=sys.stderr)
        return 2
    out = os.path.join(OUT, f"{code.rsplit('/', 1)[-1]}-stage")
    print(f"=== {name}: {scene} -> {out} ===", flush=True)
    return subprocess.run(
        [sys.executable, CONVERTER, "--project", project, "--scene", scene, "--out", out, "--name", name] + extra
    ).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="+", help="stage codes, as `ag.py stage` took them")
    args, extra = ap.parse_known_args()
    os.makedirs(OUT, exist_ok=True)
    rc = 0
    for code in args.codes:
        rc = convert(code, extra) or rc
    if rc == 0:
        print(f"\nUpload a folder under {OUT} to reze.design as a stage.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

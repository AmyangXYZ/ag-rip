#!/usr/bin/env python3
r"""
AetherGazer asset pipeline - one entry point for the whole flow.

    game bundles (.ys)
        |-- rig      -> AssetStudioModCLI  -> AG_fbx/<id>/...        (mesh + skeleton)
        |-- prefab   -> AssetRipper        -> AG_sample/<id>/...     (bind pose)
        `-- clips    -> AssetRipper        -> AG_animations/...      (.anim YAML)
                                    |
                                    v
                            AG_fbx_anim/<id>/<model>@<clip>.fbx
                                    |
                                    v            (reze-rig, separate project)
                            AG_vmd/<id>/*.vmd

From a fresh game install, once
-------------------------------
  python ag.py restore                   un-hash + de-pad the bundle cache (~51 GB)
  python ag.py rip-anims                 rip every clip -> AG_animations (hours)

Both are prerequisites for everything below and only need doing again when the
game updates.

Commands
--------
  python ag.py find 1095                 what clips exist, and where
  python ag.py find 1095 --grep skill    filter them
  python ag.py list                      every character with clip counts
  python ag.py audit 1095                prove nothing was missed

  python ag.py build 1095                rig + prefab + every clip  <- the usual one
  python ag.py build 1095 --clips stand,run

  python ag.py rig 1095                  rig FBX only      -> AG_fbx
  python ag.py prefab 1095               bind pose only    -> AG_sample
  python ag.py anim 1095                 clips -> FBX      -> AG_fbx_anim

  python ag.py scenes --grep x343        stage index (code, size, label, skins)
  python ag.py stages --char 1095        DLC skin -> stage table, with pictures
  python ag.py stage x343                stage -> Unity project  -> AG_stages/x343

  python ag.py names                     build the ID -> name contact sheet
  python ag.py rename names.json --apply relabel the extracted tree

Anything after the character ID is forwarded to the underlying tool, so
`python ag.py anim 1095 --costume 109500 --clips stand --dry-run` works.
Each tool in agtools/ is still runnable on its own; this just saves remembering
which one does what and in what order.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "agtools")

# command -> (script, help)
COMMANDS = {
    "restore": ("restore_assets.py", "un-hash and de-pad the game's bundle cache"),
    "rip-anims": ("rip_animations.py", "rip every AnimationClip -> AG_animations"),
    "find": ("find_anims.py", "list a character's animation clips"),
    "list": ("find_anims.py", "list every character with clips"),
    "audit": ("audit_clips.py", "account for every clip in the tree; show any gaps"),
    "scenes": ("scenes.py", "index and export the 3D stages"),
    "stage": ("stage_unity.py", "one stage -> standalone Unity project (AG_stages)"),
    "cams": ("export_cameras.py", "a skin's authored camera sequences (win, DLC interactions) -> camera + character FBX, JSON"),
    "stages": ("stage_catalog.py", "DLC skin -> stage table with previews (AG_stage_names)"),
    "rig": ("export_fbx.py", "export the rigged, textured FBX"),
    "prefab": ("extract_character.py", "extract the prefab (bind pose) via AssetRipper"),
    "anim": ("export_anim_fbx.py", "bind clips onto the rig -> one FBX per clip"),
    "names": ("char_icons.py", "build the character ID -> name contact sheet"),
    "rename": ("apply_names.py", "apply a name mapping to the extracted tree"),
}


def run(script: str, args: list[str]) -> int:
    return subprocess.run([sys.executable, os.path.join(TOOLS, script)] + args).returncode


def build(args: list[str]) -> int:
    """Rig, prefab and clips for one character, in the order they depend on."""
    if not args:
        print("usage: python ag.py build <id> [options forwarded to anim]",
              file=sys.stderr)
        return 2
    cid, rest = args[0], args[1:]

    steps = [
        ("rig FBX", "export_fbx.py", [cid, "--skip-existing"]),
        ("prefab", "extract_character.py", [cid]),
        ("clips -> FBX", "export_anim_fbx.py", [cid] + rest),
    ]
    have_prefab = os.path.isdir(os.path.join(ROOT, "AG_sample", cid, "ExportedProject"))
    for i, (label, script, argv) in enumerate(steps, 1):
        print(f"\n=== [{i}/{len(steps)}] {label} ===", flush=True)
        # Prefab extraction restarts AssetRipper and rewrites the whole project;
        # skip it when one is already there, as the rig step already does.
        if script == "extract_character.py" and have_prefab:
            print("already extracted; skipping")
            continue
        rc = run(script, argv)
        if rc != 0:
            print(f"\n'{label}' failed (exit {rc}); stopping.", file=sys.stderr)
            return rc
    print(f"\ndone: {cid} -> {os.path.join(ROOT, 'AG_fbx_anim', cid)}")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    cmd, args = sys.argv[1], sys.argv[2:]

    if cmd == "build":
        return build(args)
    if cmd == "list":
        return run("find_anims.py", ["--list"] + args)
    if cmd in COMMANDS:
        return run(COMMANDS[cmd][0], args)

    print(f"unknown command '{cmd}'\n", file=sys.stderr)
    print("commands:", file=sys.stderr)
    for name, (_, blurb) in COMMANDS.items():
        print(f"  {name:<8} {blurb}", file=sys.stderr)
    print("  build    rig + prefab + clips, in one go", file=sys.stderr)
    print("\nrun 'python ag.py --help' for the full flow", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
r"""
Index and export AetherGazer's 3D stages.

Layout
------
Stages live in pairs under ComScene:

    comscene/levels/<code>.ys        the scene itself - mostly MonoBehaviour, few meshes
    comscene/levels/<code>_dep.ys    its dependencies: the actual geometry and textures
    comscene/map/<code>_graph.ys     navigation graph, no art

There are ~428 codes (a01, a11b, b06h, ... q*, x*). The game ships no stage-name
table - same as characters, it lives server-side - but the bundles' *container*
paths are readable and descriptive, so a usable index can be derived from them:

    Assets/ComScene/ArtResources/Scene/A01/texture/A01_chexiang_02    che xiang = carriage
    Assets/ComScene/ArtResources/SceneV2/Building/fence/Prefabs/...   shared library

A `_dep` mixes stage-specific art (`Scene/<CODE>/`, `ScenePrefabs/<CODE>/`) with
shared set-dressing (`SceneV2/...`) reused across many stages. The index separates
the two, because the stage-specific tokens are what actually identify a stage.

Usage
-----
  python ag.py scenes --index          # build/refresh the table (a few minutes)
  python ag.py scenes                  # show the table
  python ag.py scenes --grep chexiang  # filter by code or label
  python ag.py scenes --export a01     # one stage -> FBX + textures
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

CLI = (r"C:\AetherGazerStarter\tools\AssetStudioModCLI"
       r"\AssetStudioModCLI_net8_portable\AssetStudioModCLI.exe")
ROOT = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data\StreamingAssets"
        r"\Windows_restored")
LEVELS = os.path.join(ROOT, "comscene", "levels")
# Every folder holding <code>.ys / <code>_dep.ys scene pairs. comscene is the big
# one; the others are the Q-version world, a few effect-heavy home scenes and the
# minigames. A code is unique across them except where noted by the "folder" field.
LEVEL_DIRS = {
    "comscene": LEVELS,
    "comsceneq": os.path.join(ROOT, "comsceneq", "levels"),
    "comeffect": os.path.join(ROOT, "comeffect", "levels"),
    "levels": os.path.join(ROOT, "levels"),
}
# Skin IDs (<char><2-digit skin>, e.g. 104903) in prop names and containers. This is
# how a DLC room gives itself away: x343's props are all "104903_prop_*".
SKIN_ID = re.compile(r"(?<![0-9])(1[0-2][0-9]{2}0[0-9])(?![0-9])")
OUT = r"C:\AetherGazerStarter\AG_scenes"
INDEX = os.path.join(OUT, "_scene_index.json")
UNITY_VERSION = "2022.3.62f3"

# Container segments marking stage-specific art rather than the shared library.
SPECIFIC = re.compile(r"/(?:Scene|ScenePrefabs)/([A-Za-z0-9_]+)[/.]", re.I)
SHARED = re.compile(r"/(SceneV2|Common|Tongyong)/", re.I)
# Words that carry no meaning when labelling a stage.
STOP = {"texture", "textures", "material", "materials", "prefab", "prefabs",
        "model", "models", "mesh", "meshes", "effect", "fx", "lod", "lightmap",
        "assets", "comscene", "artresources", "scene", "sceneprefabs"}


def codes() -> list[tuple[str, str]]:
    """(folder key, code) for every stage in every level folder."""
    if not os.path.isdir(LEVELS):
        sys.exit(f"error: no stage bundles at {LEVELS}")
    out = set()
    for key, d in LEVEL_DIRS.items():
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".ys"):
                stem = f[:-3]
                out.add((key, stem[:-4] if stem.endswith("_dep") else stem))
    return sorted(out)


def level_dir(code: str, folder: str = "") -> str:
    """The folder a stage lives in; comscene wins when a code appears twice."""
    if folder:
        return LEVEL_DIRS[folder]
    for d in LEVEL_DIRS.values():
        if os.path.isfile(os.path.join(d, f"{code}.ys")) or                 os.path.isfile(os.path.join(d, f"{code}_dep.ys")):
            return d
    return LEVELS


def skins_for(rows: list[dict]) -> list[str]:
    """Skin IDs named by at least two assets, most-referenced first."""
    counts: dict[str, int] = {}
    for r in rows:
        for m in set(SKIN_ID.findall(r["name"] + " " + r["container"])):
            counts[m] = counts.get(m, 0) + 1
    return [k for k, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= 2]


def asset_list(bundle: str, tmp: str) -> list[dict]:
    """Container paths for a bundle's meshes, via AssetStudioMod's asset list.

    The list only covers assets the run actually exported, so meshes go to a
    scratch folder and are deleted straight after - it is the cheapest way to see
    containers (~1 s/bundle), and `-m info` does not report them at all.
    """
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    # Textures matter as much as meshes here: a stage's geometry is mostly drawn
    # from the shared SceneV2 prop library, and what actually identifies it is its
    # own textures (Scene/<CODE>/texture/...). `--image-format none` writes the raw
    # texture data instead of encoding PNGs, which keeps this at ~1 s per bundle.
    subprocess.run([CLI, bundle, "-m", "export", "-t", "mesh,tex2d", "-g", "none",
                    "--image-format", "none", "--unity-version", UNITY_VERSION,
                    "-o", tmp, "--export-asset-list", "xml",
                    "--log-level", "error", "-r"],
                   capture_output=True, text=True)
    xml = os.path.join(tmp, "assets.xml")
    rows = []
    if os.path.isfile(xml):
        try:
            for a in ET.parse(xml).getroot().findall("Asset"):
                rows.append({"name": a.findtext("Name") or "",
                             "container": a.findtext("Container") or ""})
        except ET.ParseError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
    return rows


def label_for(code: str, rows: list[dict]) -> tuple[str, int, int]:
    """A descriptive label built from stage-specific container tokens."""
    tokens: dict[str, int] = {}
    n_specific = n_shared = 0
    for r in rows:
        c = r["container"]
        # Specific wins over shared: a stage's own folder is the stronger signal,
        # and testing SceneV2 first swallowed everything (a01 scored 0 own / 132 lib).
        if not SPECIFIC.search(c):
            if SHARED.search(c):
                n_shared += 1
            continue
        n_specific += 1
        for seg in c.split("/"):
            seg = os.path.splitext(seg)[0]
            for tok in re.split(r"[_\-. ]+", seg):
                t = tok.lower()
                if (len(t) > 2 and t not in STOP and not t.isdigit()
                        and t != code.lower()
                        and not re.fullmatch(r"[a-z]\d+[a-z]?", t)):
                    tokens[t] = tokens.get(t, 0) + 1
    top = sorted(tokens.items(), key=lambda kv: -kv[1])[:6]
    return " ".join(t for t, _ in top), n_specific, n_shared


def build_index(limit: int = 0) -> dict:
    tmp = os.path.join(OUT, "_tmp")
    os.makedirs(OUT, exist_ok=True)
    entries: dict[str, dict] = {}
    ids = codes()
    if limit:
        ids = ids[:limit]
    print(f"indexing {len(ids)} stage(s) from {len(LEVEL_DIRS)} level folders")
    for i, (folder, code) in enumerate(ids, 1):
        d = LEVEL_DIRS[folder]
        scene = os.path.join(d, f"{code}.ys")
        dep = os.path.join(d, f"{code}_dep.ys")
        rows = asset_list(dep, tmp) if os.path.isfile(dep) else []
        label, spec, shared = label_for(code, rows)
        # The scene bundle's own few meshes carry the prefab-level names, which is
        # where the skin IDs of a DLC room mostly show up.
        own = asset_list(scene, tmp) if os.path.isfile(scene) else []
        key = code if code not in entries else f"{folder}/{code}"
        entries[key] = {
            "code": code,
            "folder": folder,
            "skins": skins_for(rows + own),
            "scene_mb": round(os.path.getsize(scene) / 1e6, 2) if os.path.isfile(scene) else 0.0,
            "dep_mb": round(os.path.getsize(dep) / 1e6, 2) if os.path.isfile(dep) else 0.0,
            "meshes": len(rows),
            "specific": spec,
            "shared": shared,
            "label": label,
        }
        if i % 25 == 0 or i == len(ids):
            print(f"  {i}/{len(ids)}...", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    if not limit:
        with open(INDEX, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=1, ensure_ascii=False)
        print(f"-> {INDEX}")
    return entries


def load_index() -> dict:
    if not os.path.isfile(INDEX):
        sys.exit("no index yet - run: python ag.py scenes --index")
    with open(INDEX, encoding="utf-8") as fh:
        return json.load(fh)


def export_stage(code: str, out_dir: str) -> int:
    """Export one stage's geometry and textures to FBX.

    Both bundles go in as one input folder rather than two separate runs, so the
    assets they share are resolved once.

    Two things to expect in the output. Objects whose names collide across
    containers are written as "<name>" and "<name> (1)"; those were compared and
    are genuinely different meshes, not copies, so nothing is pruned. And this does
    NOT give you placement - splitObjects writes every object at the origin, so the
    result is a prop library, not an assembled stage. The scene graph lives in the
    MonoBehaviour/Transform data, which only AssetRipper reconstructs (the route
    extract_character.py already uses for characters).
    """
    os.makedirs(out_dir, exist_ok=True)
    stage = os.path.join(out_dir, "_bundles")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    found = False
    for suffix in ("", "_dep"):
        b = os.path.join(level_dir(code), f"{code}{suffix}.ys")
        if os.path.isfile(b):
            shutil.copyfile(b, os.path.join(stage, os.path.basename(b)))
            found = True
    if not found:
        print(f"  ! no bundles for {code}")
        return 0

    proc = subprocess.run([CLI, stage, "-m", "splitObjects", "-g", "containerFull",
                           "--unity-version", UNITY_VERSION, "-o", out_dir,
                           "--log-level", "warning", "-r"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-2:]
        print(f"  ! {code}: rc={proc.returncode} {tail}")
    shutil.rmtree(stage, ignore_errors=True)
    # No dedupe pass: objects that share a name across containers ("<name>" and
    # "<name> (1)") were checked and are genuinely different meshes, not copies.
    return sum(1 for _, _, fs in os.walk(out_dir) for f in fs if f.endswith(".fbx"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", action="store_true", help="rebuild the stage table")
    ap.add_argument("--grep", help="filter by code or label")
    ap.add_argument("--export", metavar="CODE", help="export one stage to FBX")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--limit", type=int, default=0, help="index only N stages (testing)")
    args = ap.parse_args()

    if args.export:
        d = os.path.join(args.out, args.export)
        n = export_stage(args.export, d)
        print(f"{args.export}: {n} fbx -> {d}")
        return 0

    entries = build_index(args.limit) if args.index else load_index()
    rows = sorted(entries.values(), key=lambda e: -e["dep_mb"])
    if args.grep:
        q = args.grep.lower()
        rows = [r for r in rows if q in r["code"].lower() or q in r["label"].lower()
                or any(q in k for k in r.get("skins", []))]

    print(f"\n{'code':<9}{'dep MB':>8}{'mesh':>6}{'own':>5}{'lib':>5}  label")
    for r in rows:
        print(f"{r['code']:<9}{r['dep_mb']:>8.1f}{r['meshes']:>6}"
              f"{r['specific']:>5}{r['shared']:>5}  {r['label']}")
    print(f"\n{len(rows)} stage(s), total dep {sum(r['dep_mb'] for r in rows) / 1024:.1f} GB")
    if rows:
        print(f"export one with:  python ag.py scenes --export {rows[0]['code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

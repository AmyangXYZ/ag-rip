#!/usr/bin/env python3
r"""
Stage catalogue: which character's DLC skin goes with which stage, with pictures.

The game has no readable skin -> home-scene table (it lives in the client Lua,
which ships encrypted), so the links are *derived*, and every link records how:

  named   the stage's own assets carry the skin ID (x343's "104903_prop_huima_001"),
          or a bundle name pairs them ("ch_102003_x331_object_001_fx")
  deps    the stage's dependency closure pulls in a bundle named after the skin
  manual  set by hand in AG_stage_names/links.json - wins over everything

Pictures come straight from the game:
  textureconfig/scenechangeui/item/<skin>   the home-scene switcher card per DLC skin
  textureconfig/scenechangeui/item/<6xxx>   ...and per non-character home scene
  textureconfig/scenechangeui/bg/<6xxx>     full-size backgrounds of those
  textureconfig/stage_s/<code>              stage-select thumbnails (battle stages)

Output (AG_stage_names/, laid out like AG_names/):
  dlc/<skin>.png  home/<id>.png  stage/<code>.png
  catalog.json    everything below, machine-readable
  contact_sheet.html

Usage
-----
  python agtools/stage_catalog.py            rebuild (reuses exported pictures)
  python agtools/stage_catalog.py --char 1095
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenes  # noqa: E402

ROOT = scenes.ROOT
OUT = r"C:\AetherGazerStarter\AG_stage_names"
SCENES = r"C:\AetherGazerStarter\AG_scenes"
INDEX = os.path.join(SCENES, "_scene_index.json")
DEPS = os.path.join(SCENES, "_stage_deps.json")
TEX = os.path.join(ROOT, "textureconfig")
PICS = {  # output folder -> game texture folder
    "dlc": os.path.join(TEX, "scenechangeui", "item"),
    "home": os.path.join(TEX, "scenechangeui", "bg"),
    "stage": os.path.join(TEX, "stage_s"),
}
PAIR = re.compile(r"(1[0-2]\d{2}0\d)_(x\d{2,4}[a-z]?)")

# In-battle "modifier mode" spaces, one per pantheon. From Config.bytes table
# SourceSpace (id, name -> Effect/SourceSpaceScene/Xnn); these are effect
# prefabs under comeffect/effect/sourcespacescene, not level scenes.
SOURCE_SPACES = [
    (0, "通用", "generic", "x01"), (1, "奥山", "Olympus", "x05"),
    (2, "尼罗", "Nile / Egypt", "x03"), (3, "真樱", "Japan", "x02"),
    (4, "圣树", "World Tree / Norse", "x04"), (5, "众星", "Stars", "x06"),
    (9, "天垣", "Celestial / China", "x07"),
]


def export_pictures() -> None:
    """Texture bundles -> PNG, once; the game's own names are kept."""
    for sub, src in PICS.items():
        dst = os.path.join(OUT, sub)
        if os.path.isdir(dst) and os.listdir(dst):
            continue
        tmp = os.path.join(OUT, "_tmp", sub)
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run([scenes.CLI, src, "-m", "export", "-t", "tex2d", "-g", "none",
                        "--image-format", "png", "--unity-version", scenes.UNITY_VERSION,
                        "-o", tmp, "--log-level", "error", "-r"], capture_output=True)
        os.makedirs(dst, exist_ok=True)
        for dp, _, fns in os.walk(tmp):
            for fn in fns:
                # "$naive" is the censored variant; "_#n" a duplicate of the same image
                if fn.endswith(".png") and "$naive" not in fn and "_#" not in fn:
                    shutil.copyfile(os.path.join(dp, fn), os.path.join(dst, fn.lower()))
    shutil.rmtree(os.path.join(OUT, "_tmp"), ignore_errors=True)


def ids_in(folder: str) -> list[str]:
    d = os.path.join(OUT, folder)
    return sorted(f[:-4] for f in os.listdir(d) if f.endswith(".png")) if os.path.isdir(d) else []


def evidence() -> dict[str, collections.Counter]:
    """skin -> Counter(stage -> score)."""
    ev: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    if os.path.isfile(INDEX):
        for key, e in json.load(open(INDEX, encoding="utf-8")).items():
            # A stage's first skin is the one it is built around; later ones are
            # borrowed props (x343 also shows 104401's sign and 102003's screen).
            for rank, s in enumerate(e.get("skins", [])):
                ev[s][key] += 50 if rank == 0 else 10
    if os.path.isfile(DEPS):
        for key, d in json.load(open(DEPS, encoding="utf-8")).items():
            for b in d["bundles"]:
                for s in set(scenes.SKIN_ID.findall(os.path.basename(b))):
                    ev[s][key] += 1
    for dp, _, fns in os.walk(ROOT):
        for fn in fns:
            for s, x in PAIR.findall(fn.lower()):
                # A "ch_<skin>_<stage>_..." prop is that skin's item placed in the
                # stage - a real link, but weaker than the stage being built on it.
                ev[s][x] += 30
    return ev


def build() -> dict:
    export_pictures()
    links_path = os.path.join(OUT, "links.json")
    manual = json.load(open(links_path, encoding="utf-8")) if os.path.isfile(links_path) else {}
    ev = evidence()
    index = json.load(open(INDEX, encoding="utf-8")) if os.path.isfile(INDEX) else {}

    dlc = {}
    for skin in ids_in("dlc"):
        if not skin.isdigit() or len(skin) != 6:
            continue
        cands = ev.get(skin, collections.Counter()).most_common(5)
        m = manual.get(skin, {})
        if m.get("stage"):
            stage, how = m["stage"], "manual"
        elif cands:
            stage = cands[0][0]
            how = "named" if cands[0][1] >= 30 else "deps"
        else:
            stage, how = "", ""
        dlc[skin] = {"char": skin[:4], "stage": stage, "how": how,
                     "candidates": [f"{k}:{n}" for k, n in cands],
                     "note": m.get("note", ""), "picture": f"dlc/{skin}.png"}
    claimed = collections.Counter(d["stage"] for d in dlc.values() if d["stage"])
    for d in dlc.values():
        if d["stage"] and claimed[d["stage"]] > 1 and d["how"] != "manual":
            d["how"] += " (shared)"
    home = {i: {"picture": f"home/{i}.png" if os.path.isfile(os.path.join(OUT, "home", f"{i}.png"))
                else f"dlc/{i}.png"}
            for i in sorted(set(ids_in("home")) | {x for x in ids_in("dlc") if len(x) == 4})}
    stages = {}
    for key, e in sorted(index.items()):
        pic = f"stage/{e['code']}.png"
        ren = [f"render/{key}_{v}.png" for v in ("high", "eye")
               if os.path.isfile(os.path.join(OUT, "render", f"{key}_{v}.png"))]
        stages[key] = {**e, "picture": pic if os.path.isfile(os.path.join(OUT, pic)) else "",
                       "renders": ren,
                       "claimed_by": sorted(k for k, d in dlc.items() if d["stage"] == key)}
    cat = {"dlc": dlc, "home": home, "stages": stages,
           "source_spaces": [{"id": i, "zh": zh, "en": en, "prefab": x,
                              "bundle": f"comeffect/effect/sourcespacescene/{x}.ys"}
                             for i, zh, en, x in SOURCE_SPACES]}
    with open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump(cat, fh, indent=1, ensure_ascii=False)
    if not os.path.isfile(links_path):
        with open(links_path, "w", encoding="utf-8") as fh:
            json.dump({s: {"stage": "", "note": ""} for s in dlc}, fh, indent=1)
    write_sheet(cat)
    return cat


def write_sheet(cat: dict) -> None:
    e = html.escape
    rows = []
    for skin, d in cat["dlc"].items():
        st = d["stage"]
        rows.append(
            f"<tr><td><img src='{e(d['picture'])}' loading=lazy></td>"
            f"<td><b>{skin}</b><br>char {d['char']}</td>"
            f"<td><b>{e(st) or '—'}</b><br><small>{e(d['how'])}</small></td>"
            f"<td><small>{e(' '.join(d['candidates']))}</small><br>{e(d['note'])}</td></tr>")
    homes = "".join(f"<figure><img src='{e(h['picture'])}' loading=lazy><figcaption>{i}"
                    f"</figcaption></figure>" for i, h in cat["home"].items())
    stages = "".join(
        f"<figure><img src='{e(s['picture'])}' loading=lazy><figcaption>{e(k)} "
        f"<small>{e(s.get('label', ''))}</small></figcaption></figure>"
        for k, s in cat["stages"].items() if s["picture"])
    rooms = "".join(
        f"<figure>{''.join(f'<img src={chr(39)}{e(r)}{chr(39)} loading=lazy>' for r in s['renders'])}"
        f"<figcaption><b>{e(k)}</b> {e(', '.join(s['claimed_by']) or 'unclaimed')} "
        f"<small>{e(s.get('label', ''))}</small></figcaption></figure>"
        for k, s in cat["stages"].items() if s["renders"])
    spaces = "".join(f"<li>{s['id']} {s['zh']} ({s['en']}) → {s['prefab']}</li>"
                     for s in cat["source_spaces"])
    page = f"""<!doctype html><meta charset=utf-8><title>AetherGazer stages</title>
<style>body{{font:13px system-ui;margin:16px;background:#fafafa}}
table{{border-collapse:collapse}}td{{border-bottom:1px solid #ddd;padding:4px 8px;vertical-align:top}}
td img{{width:260px}}figure{{display:inline-block;margin:4px;width:240px}}
figure img{{width:240px}}figcaption{{font-size:12px}}</style>
<h1>DLC skins → stages</h1><p>how: named = skin ID in the stage's own assets;
deps = via its dependencies; manual = links.json.</p>
<table>{''.join(rows)}</table>
<h1>Stage renders (flat-lit, from the exported scene)</h1>{rooms}
<h1>Home scenes (non-character)</h1>{homes}
<h1>Modifier mode — pantheon spaces</h1><ul>{spaces}</ul>
<h1>Stage thumbnails</h1>{stages}"""
    with open(os.path.join(OUT, "contact_sheet.html"), "w", encoding="utf-8") as fh:
        fh.write(page)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--char", help="show one character's DLC skins")
    args = ap.parse_args()
    cat = build()
    shown = {k: v for k, v in cat["dlc"].items() if not args.char or v["char"] == args.char}
    print(f"{'skin':<8}{'stage':<14}{'how':<8}candidates")
    for skin, d in shown.items():
        print(f"{skin:<8}{d['stage'] or '-':<14}{d['how']:<8}{' '.join(d['candidates'][:4])}")
    linked = sum(1 for d in cat["dlc"].values() if d["stage"])
    print(f"\n{linked}/{len(cat['dlc'])} DLC skins linked; {len(cat['home'])} home scenes; "
          f"{sum(1 for s in cat['stages'].values() if s['picture'])} stages with thumbnails")
    print(f"-> {os.path.join(OUT, 'contact_sheet.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

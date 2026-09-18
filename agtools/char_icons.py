#!/usr/bin/env python3
r"""
Build the character ID -> name mapping the game refuses to ship.

AetherGazer stores characters by numeric ID everywhere; the display-name table is
not in StreamingAssets, DataBase, or the Unity persistent data - it comes from the
server. Community wikis list the names but not the internal IDs, so there is no
table to download either.

What the game *does* ship is per-character portrait art keyed by ID, under
textureconfig/character/{icon,portrait}/<id>.ys. This extracts one image per
character that has animation clips and writes a contact sheet: open it, read off
the names, and save the result as names.json. One pass over ~131 faces.

Usage
-----
  python char_icons.py                    # extract icons + write contact sheet
  python char_icons.py --art portrait     # full illustrations instead of head icons
  python char_icons.py --open             # ...and open the sheet in a browser

Then fill in names.json (a template is written next to the sheet) and it is picked
up automatically by find_anims.py, and usable by apply_names.py to relabel the tree.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import webbrowser

CLI = (r"C:\AetherGazerStarter\tools\AssetStudioModCLI"
       r"\AssetStudioModCLI_net8_portable\AssetStudioModCLI.exe")
ART = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data\StreamingAssets"
       r"\Windows_restored\textureconfig\character")
HERO_ROOT = (r"C:\AetherGazerStarter\AG_animations\ComChar\ArtResources"
             r"\Char\Hero")
OUT = r"C:\AetherGazerStarter\AG_names"
UNITY_VERSION = "2022.3.62f3"
NAMES = r"C:\AetherGazerStarter\names.json"


def hero_ids() -> list[str]:
    if not os.path.isdir(HERO_ROOT):
        sys.exit(f"error: no animation tree at {HERO_ROOT}")
    return sorted(d for d in os.listdir(HERO_ROOT)
                  if d.isdigit() and os.path.isdir(os.path.join(HERO_ROOT, d)))


def extract(ids: list[str], art: str, out_dir: str, overwrite: bool) -> dict[str, str]:
    """Pull one image per character. Returns id -> image filename.

    No single art set covers everyone - the head icons miss most NPC/boss models -
    so fall back through the other sets rather than leaving a card blank.
    """
    chain = [art] + [a for a in ("icon", "mediumicon", "littleicon", "portrait",
                                 "itemshead") if a != art]
    chain = [a for a in chain if os.path.isdir(os.path.join(ART, a))]
    if not chain:
        sys.exit(f"error: no art sets found under {ART}")
    os.makedirs(out_dir, exist_ok=True)

    found: dict[str, str] = {}
    todo = []
    for cid in ids:
        existing = [f for f in os.listdir(out_dir)
                    if f.startswith(cid + ".") and f.lower().endswith(".png")]
        if existing and not overwrite:
            found[cid] = existing[0]
            continue
        for a in chain:
            bundle = os.path.join(ART, a, f"{cid}.ys")
            if os.path.isfile(bundle):
                todo.append((cid, bundle))
                break

    for i, (cid, bundle) in enumerate(todo, 1):
        stage = os.path.join(out_dir, "_tmp")
        proc = subprocess.run(
            [CLI, bundle, "-m", "export", "-t", "tex2d,sprite", "-g", "none",
             "--unity-version", UNITY_VERSION, "-o", stage,
             "--log-level", "error", "-r"],
            capture_output=True, text=True)
        pngs = []
        for dp, _, fns in os.walk(stage):
            pngs += [os.path.join(dp, f) for f in fns if f.lower().endswith(".png")]
        if pngs:
            # Biggest image is the portrait itself, not a decoration.
            best = max(pngs, key=os.path.getsize)
            dst = os.path.join(out_dir, f"{cid}.png")
            os.replace(best, dst)
            found[cid] = os.path.basename(dst)
        elif proc.returncode != 0:
            print(f"  {cid}: extract failed", file=sys.stderr)
        for dp, _, fns in os.walk(stage, topdown=False):
            for f in fns:
                os.remove(os.path.join(dp, f))
            os.rmdir(dp)
        if i % 20 == 0:
            print(f"  {i}/{len(todo)}...", flush=True)
    return found


def clip_counts(ids: list[str]) -> dict[str, int]:
    counts = {}
    for cid in ids:
        n = 0
        cdir = os.path.join(HERO_ROOT, cid)
        for folder in os.listdir(cdir):
            fdir = os.path.join(cdir, folder)
            if os.path.isdir(fdir):
                n += sum(1 for f in os.listdir(fdir) if f.endswith(".anim"))
        counts[cid] = n
    return counts


def write_sheet(path: str, ids: list[str], images: dict[str, str],
                counts: dict[str, int], known: dict[str, str]) -> None:
    def card(cid: str) -> str:
        img = images.get(cid)
        thumb = (f'<img src="{html.escape(img)}" loading="lazy" alt="{cid}">'
                 if img else '<div class="noimg">no art</div>')
        name = html.escape(known.get(cid, ""))
        return (f'<figure><div class="pic">{thumb}</div>'
                f'<figcaption><b>{cid}</b><span>{counts.get(cid, 0)} clips</span>'
                f'<input data-id="{cid}" value="{name}" placeholder="name..."></figcaption>'
                f"</figure>")

    # IDs starting with 1 are the playable roster; 6xxx are NPC/boss/servant models.
    playable = [c for c in ids if c.startswith("1")]
    others = [c for c in ids if not c.startswith("1")]
    cards = [f'<h2>Playable &mdash; {len(playable)}</h2><div class="grid">',
             "".join(card(c) for c in playable), "</div>"]
    if others:
        cards += [f'<h2>NPC / boss / servant &mdash; {len(others)}</h2><div class="grid">',
                  "".join(card(c) for c in others), "</div>"]

    doc = f"""<!doctype html><meta charset="utf-8">
<title>AetherGazer character IDs</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px;
      background:#14151a;color:#e8e8ea}}
 h1{{font-size:18px;margin:0 0 4px}}
 p{{color:#9a9aa6;margin:0 0 20px;max-width:70ch}}
 .bar{{position:sticky;top:0;background:#14151a;padding:12px 0;border-bottom:1px solid #2a2b33;
      margin-bottom:20px;z-index:2;display:flex;gap:10px;align-items:center}}
 button{{background:#3b6ef5;color:#fff;border:0;border-radius:6px;padding:8px 14px;
        font:inherit;cursor:pointer}}
 button:hover{{background:#5581f7}}
 #out{{width:100%;height:150px;background:#0e0f13;color:#b9c7e6;border:1px solid #2a2b33;
      border-radius:6px;font:12px/1.4 ui-monospace,monospace;padding:10px;margin-top:12px}}
 h2{{font-size:14px;font-weight:600;color:#9a9aa6;margin:26px 0 12px;
    text-transform:uppercase;letter-spacing:.06em}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}}
 figure{{margin:0;background:#1c1d24;border:1px solid #2a2b33;border-radius:8px;
        overflow:hidden}}
 .pic{{height:150px;display:flex;align-items:center;justify-content:center;
      background:#0e0f13}}
 .pic img{{max-width:100%;max-height:150px;object-fit:contain}}
 .noimg{{color:#55565f;font-size:12px}}
 figcaption{{padding:8px;display:flex;flex-direction:column;gap:4px}}
 figcaption span{{color:#8a8a95;font-size:12px}}
 input{{background:#0e0f13;border:1px solid #33343d;border-radius:4px;color:#e8e8ea;
       padding:5px 7px;font:inherit;width:100%;box-sizing:border-box}}
 input:focus{{outline:none;border-color:#3b6ef5}}
</style>
<h1>AetherGazer character IDs &mdash; {len(ids)} with animation clips</h1>
<p>Type each character's name under their portrait, then hit Copy and paste the result
into <code>names.json</code> next to the scripts. Blank entries are skipped, so you can
do this in several sittings &mdash; reopening this page keeps what is already in
<code>names.json</code>.</p>
<div class="bar"><button onclick="dump()">Copy names.json</button>
<span id="msg"></span></div>
<textarea id="out" readonly placeholder="output appears here"></textarea>
{''.join(cards)}
<script>
function dump(){{
  const o={{}};
  document.querySelectorAll('input[data-id]').forEach(i=>{{
    const v=i.value.trim(); if(v) o[i.dataset.id]=v;
  }});
  const t=JSON.stringify(o,null,1);
  document.getElementById('out').value=t;
  navigator.clipboard.writeText(t).then(
    ()=>document.getElementById('msg').textContent=
        Object.keys(o).length+' names copied to clipboard',
    ()=>document.getElementById('msg').textContent=
        Object.keys(o).length+' names ready below');
}}
</script>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--art", default="icon",
                    choices=["icon", "mediumicon", "littleicon", "portrait"],
                    help="which art set to pull (default: icon)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--open", action="store_true", help="open the sheet when done")
    args = ap.parse_args()

    if not os.path.isfile(CLI):
        sys.exit(f"error: AssetStudioModCLI not found at {CLI}")

    ids = hero_ids()
    print(f"{len(ids)} characters have animation clips; pulling '{args.art}' art")
    images = extract(ids, args.art, args.out, args.overwrite)
    print(f"got art for {len(images)}/{len(ids)}")

    known = {}
    if os.path.isfile(NAMES):
        with open(NAMES, encoding="utf-8-sig") as fh:
            known = {str(k): str(v) for k, v in json.load(fh).items()}
        print(f"carrying over {len(known)} names already in {NAMES}")

    sheet = os.path.join(args.out, "contact_sheet.html")
    write_sheet(sheet, ids, images, clip_counts(ids), known)
    print(f"\ncontact sheet -> {sheet}")

    template = os.path.join(args.out, "names_template.json")
    with open(template, "w", encoding="utf-8") as fh:
        json.dump({cid: known.get(cid, "") for cid in ids}, fh, indent=1)
    print(f"blank template -> {template}")
    print(f"\nfill it in, save as {NAMES}, then:  python find_anims.py <name>")

    if args.open:
        webbrowser.open("file:///" + sheet.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

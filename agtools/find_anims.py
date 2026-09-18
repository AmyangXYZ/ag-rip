#!/usr/bin/env python3
r"""
Find the animation clips belonging to one AetherGazer character.

Accepts a numeric ID (1044) or a name (osiris, case-insensitive, partial match)
if you have a mapping file - see names.json / char_icons.py for building one.
AetherGazer ships no ID->name table anywhere in StreamingAssets; it lives on the
server, so names only work once you supply them.

Clips live under AG_animations in per-costume folders:

    ComChar/ArtResources/Char/Hero/1044/104400/attack1.anim
                                     ^id  ^id+costume+suffix

Suffixes: none = battle model, 'ui' = menu/portrait model, 'display' = showcase.

Usage
-----
  python ag.py list                      # every character, clip counts
  python ag.py find 1095                 # one character's clips
  python ag.py find osiris               # by name (needs names.json)
  python ag.py find 1095 --grep skill    # only clips matching 'skill'
  python ag.py find 1095 --costume 109502    # one costume only
  python ag.py find 1095 --paths         # print full .anim paths, nothing else
  python ag.py find 1095 --json          # machine-readable
  python ag.py find --reindex            # rebuild the cached index

The index is cached in AG_animations/_anim_index.json because the tree is ~22 GB;
a cold walk takes a while, a cached lookup is instant.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ANIM_ROOT = r"C:\AetherGazerStarter\AG_animations"
HERO_ROOT = os.path.join(ANIM_ROOT, "ComChar", "ArtResources", "Char", "Hero")
INDEX = os.path.join(ANIM_ROOT, "_anim_index.json")
NAMES = r"C:\AetherGazerStarter\names.json"

SUFFIX_LABEL = {"": "battle", "ui": "menu/ui", "display": "showcase",
                "capture": "photo mode", "dorm": "dorm", "sp": "special",
                "q": "chibi"}


def load_names() -> dict[str, str]:
    if not os.path.isfile(NAMES):
        return {}
    with open(NAMES, encoding="utf-8-sig") as fh:
        raw = json.load(fh)
    return {str(k): str(v) for k, v in raw.items() if str(v).strip()}


def build_index() -> dict:
    """Walk the hero tree once and record clips per character per costume folder."""
    if not os.path.isdir(HERO_ROOT):
        sys.exit(f"error: no animation tree at {HERO_ROOT}\n"
                 f"       run the AssetRipper extraction first")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import clip_sets

    chars: dict[str, dict] = {}
    for cid in sorted(os.listdir(HERO_ROOT)):
        cdir = os.path.join(HERO_ROOT, cid)
        if not (cid.isdigit() and os.path.isdir(cdir)):
            continue
        # Same discovery the exporter uses, so what `list` counts is what `build`
        # will actually export - they disagreed before, over folders that don't
        # carry the character's id.
        sets = {s["label"]: [os.path.splitext(c)[0] for c in s["clips"]]
                for s in clip_sets.main_sets(cid)}
        controllers = sorted(f for f in os.listdir(cdir) if f.endswith(".controller"))
        if sets:
            chars[cid] = {"sets": sets, "controllers": controllers, "extra": 0}

    # Clip sets outside the hero tree, counted here so `list` totals match what
    # `build` will actually export. One walk for everyone rather than 131.
    for cid, entries in clip_sets.all_extra_sets().items():
        if cid in chars:
            chars[cid]["extra"] = sum(len(e["clips"]) for e in entries)

    return {"root": HERO_ROOT, "chars": chars}


def load_index(reindex: bool = False) -> dict:
    if not reindex and os.path.isfile(INDEX):
        try:
            with open(INDEX, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    idx = build_index()
    try:
        with open(INDEX, "w", encoding="utf-8") as fh:
            json.dump(idx, fh, indent=1)
    except OSError:
        pass
    return idx


def resolve(query: str, chars: dict, names: dict[str, str]) -> list[str]:
    """A query is an ID, an ID prefix, or a (partial) name."""
    if query.isdigit():
        if query in chars:
            return [query]
        hits = [c for c in chars if c.startswith(query)]
        if hits:
            return hits
        return []
    q = query.lower()
    exact = [cid for cid, nm in names.items() if nm.lower() == q and cid in chars]
    if exact:
        return exact
    return sorted(cid for cid, nm in names.items() if q in nm.lower() and cid in chars)


def describe(cid: str, entry: dict, names: dict[str, str]) -> str:
    total = sum(len(v) for v in entry["sets"].values())
    extra = entry.get("extra", 0)
    name = names.get(cid)
    tail = f" + {extra} outside the hero tree" if extra else ""
    return (f"{cid}{' ' + name if name else ''}  "
            f"({total} clips in {len(entry['sets'])} sets{tail})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="character ID or name")
    ap.add_argument("--list", action="store_true", help="list all characters and exit")
    ap.add_argument("--grep", help="only clips whose name matches this regex")
    ap.add_argument("--costume", help="only this clip folder, e.g. 104402")
    ap.add_argument("--paths", action="store_true", help="print .anim paths only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--reindex", action="store_true", help="rebuild the cached index")
    args = ap.parse_args()

    names = load_names()
    idx = load_index(args.reindex)
    chars = idx["chars"]

    if args.reindex and not args.query and not args.list:
        print(f"indexed {len(chars)} characters, "
              f"{sum(sum(len(v) for v in c['sets'].values()) for c in chars.values())} clips"
              f" -> {INDEX}")
        return 0

    if args.list or not args.query:
        print(f"{len(chars)} characters with animation clips"
              f"{'' if names else '  (no names.json - IDs only)'}\n")
        for cid in sorted(chars):
            print("  " + describe(cid, chars[cid], names))
        if not names:
            print(f"\nno name mapping at {NAMES}")
            print("build one with:  python char_icons.py")
        return 0

    hits = resolve(args.query, chars, names)
    if not hits:
        print(f"no character matched '{args.query}'", file=sys.stderr)
        if not args.query.isdigit() and not names:
            print(f"(no {os.path.basename(NAMES)} loaded, so names cannot be resolved - "
                  f"run char_icons.py to build one)", file=sys.stderr)
        return 1
    if len(hits) > 1 and not args.json:
        print(f"'{args.query}' matched {len(hits)} characters:")
        for cid in hits:
            print("  " + describe(cid, chars[cid], names))
        print("\nnarrow it down, or pass an exact ID")
        return 1

    cid = hits[0]
    entry = chars[cid]
    pat = re.compile(args.grep, re.I) if args.grep else None

    out = {"id": cid, "name": names.get(cid), "sets": {}}
    for folder, clips in entry["sets"].items():
        if args.costume and folder != args.costume and not folder.endswith(args.costume):
            continue
        keep = [c for c in clips if not pat or pat.search(c)]
        if keep:
            out["sets"][folder] = keep

    if args.json:
        out["dir"] = os.path.join(HERO_ROOT, cid)
        print(json.dumps(out, indent=1))
        return 0

    if args.paths:
        for folder, clips in out["sets"].items():
            for c in clips:
                print(os.path.join(HERO_ROOT, cid, folder, c + ".anim"))
        return 0

    label = f"{cid}" + (f" - {names[cid]}" if cid in names else "")
    total = sum(len(v) for v in out["sets"].values())
    print(f"{label}    {total} clip(s) in {len(out['sets'])} set(s)")
    print(f"source: {os.path.join(HERO_ROOT, cid)}\n")
    if not out["sets"]:
        print("  nothing matched that filter")
        return 1

    for folder, clips in out["sets"].items():
        tail = folder[len(cid):]
        suffix = tail[2:]
        kind = SUFFIX_LABEL.get(suffix, suffix or "battle")
        print(f"  {folder}  [{kind}]  {len(clips)} clips")
        line = "    "
        for c in clips:
            if len(line) + len(c) > 92:
                print(line)
                line = "    "
            line += c + "  "
        if line.strip():
            print(line)
        print()

    if entry["controllers"]:
        print(f"  controllers: {', '.join(entry['controllers'])}\n")

    # Costume DLC poses sit outside the hero tree but drive the same rig, so they
    # are exported alongside the rest and are worth showing here.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import clip_sets
    extra = clip_sets.extra_sets(cid)
    if extra and not args.costume and not pat:
        print("  also animating this rig, from outside the hero tree:")
        for s in extra:
            names = ", ".join(os.path.splitext(c)[0] for c in s["clips"])
            print(f"  {s['label']}  [{s['costume']}]  {len(s['clips'])} clips")
            print(f"    {names}\n")
        print(f"  ({sum(len(s['clips']) for s in extra)} extra clips, included by "
              f"default; --no-extra skips them)\n")

    print("export every clip above to FBX (rig + prefab handled for you):")
    print(f"  python ag.py build {cid}")
    if args.grep or args.costume:
        narrowed = f"  python ag.py build {cid}"
        if args.costume:
            narrowed += f" --costume {list(out['sets'])[0]}"
        if args.grep:
            narrowed += f" --clips {','.join(next(iter(out['sets'].values()))[:3])}"
        print(f"\njust this selection:\n{narrowed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

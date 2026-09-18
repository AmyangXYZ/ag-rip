#!/usr/bin/env python3
"""
Apply a character ID -> name mapping over the extracted AetherGazer animation tree.

AetherGazer stores characters by numeric ID only; the display-name table is not
present anywhere in StreamingAssets (it lives in the game binary or on the server),
so the extraction ships with raw IDs. If you later obtain a mapping, run this to
relabel the tree in place.

Mapping file: either JSON  {"1044": "Osiris", "1150": "Sakura"}
              or CSV/TSV   1044,Osiris        (one pair per line, '#' comments ok)

Folders whose name is exactly an ID become "<id>_<Name>":

    ComChar/ArtResources/Char/1044/   ->   ComChar/ArtResources/Char/1044_Osiris/

IDs keep their numeric prefix on purpose: the ID stays greppable and cross-checkable
against the original bundles, and the rename stays reversible.

Usage
-----
  python apply_names.py names.json                 # dry run, shows what would change
  python apply_names.py names.json --apply         # do it
  python apply_names.py names.json --apply --files # also rename file prefixes
  python apply_names.py --list-ids                 # just report the IDs present
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

DEFAULT_ROOT = r"C:\AetherGazerStarter\AG_animations"
ID_DIR = re.compile(r"^(\d{4,6})$")
ID_PREFIX = re.compile(r"^(\d{4,6})(?=[@_\-.])")


def longpath(p: str) -> str:
    p = os.path.abspath(p)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p


def load_mapping(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read().strip()
    if text.startswith("{"):
        raw = json.loads(text)
    else:
        raw = {}
        for row in csv.reader(text.splitlines(), delimiter="," if "," in text else "\t"):
            if len(row) >= 2 and row[0].strip() and not row[0].lstrip().startswith("#"):
                raw[row[0].strip()] = row[1].strip()
    clean = {}
    for k, v in raw.items():
        k = str(k).strip()
        v = re.sub(r'[<>:"/\\|?*]', "", str(v)).strip().replace(" ", "_")
        if k and v:
            clean[k] = v
    return clean


def ids_present(root: str) -> Counter:
    found = Counter()
    for dp, dns, fns in os.walk(root):
        for d in dns:
            m = ID_DIR.match(d)
            if m:
                found[m.group(1)] += 1
        for f in fns:
            m = ID_PREFIX.match(f)
            if m:
                found[m.group(1)] += 1
    return found


def resolve(mapping: dict[str, str], num: str) -> str | None:
    """Character IDs are 4 digits; 6-digit values are <char><costume>."""
    if num in mapping:
        return mapping[num]
    if len(num) == 6 and num[:4] in mapping:
        return mapping[num[:4]]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mapping", nargs="?", help="JSON or CSV of id -> name")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--apply", action="store_true", help="perform the rename")
    ap.add_argument("--files", action="store_true", help="also rename file prefixes")
    ap.add_argument("--list-ids", action="store_true",
                    help="just list numeric IDs present and exit")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print(f"error: no such folder {args.root}", file=sys.stderr)
        return 2

    if args.list_ids:
        found = ids_present(args.root)
        print(f"{len(found)} distinct numeric IDs under {args.root}\n")
        for num, n in found.most_common():
            print(f"  {num:<8} {n:>6} occurrences")
        return 0

    if not args.mapping:
        ap.error("a mapping file is required unless --list-ids is given")
    mapping = load_mapping(args.mapping)
    print(f"loaded {len(mapping)} names from {args.mapping}")

    renames, skipped = [], Counter()

    # Deepest-first so renaming a parent never invalidates a queued child path.
    for dp, dns, fns in os.walk(args.root, topdown=False):
        if args.files:
            for f in fns:
                m = ID_PREFIX.match(f)
                if not m:
                    continue
                name = resolve(mapping, m.group(1))
                if not name:
                    skipped[m.group(1)] += 1
                    continue
                new = f"{m.group(1)}_{name}{f[m.end():]}"
                if new != f:
                    renames.append((os.path.join(dp, f), os.path.join(dp, new)))
        for d in dns:
            m = ID_DIR.match(d)
            if not m:
                continue
            name = resolve(mapping, m.group(1))
            if not name:
                skipped[m.group(1)] += 1
                continue
            new = f"{m.group(1)}_{name}"
            if new != d:
                renames.append((os.path.join(dp, d), os.path.join(dp, new)))

    print(f"{len(renames)} paths to rename; {len(skipped)} IDs had no mapping")
    for src, dst in renames[:25]:
        print(f"   {os.path.relpath(src, args.root)}  ->  {os.path.basename(dst)}")
    if len(renames) > 25:
        print(f"   ... and {len(renames) - 25} more")
    if skipped:
        print("\nunmapped IDs (add these to your mapping file):")
        for num, n in skipped.most_common(20):
            print(f"   {num:<8} {n:>6} occurrences")

    if not args.apply:
        print("\ndry run - nothing changed. Re-run with --apply to commit.")
        return 0

    done = failed = 0
    for src, dst in renames:
        try:
            if os.path.exists(longpath(dst)):
                print(f"   skip (target exists): {dst}")
                continue
            os.rename(longpath(src), longpath(dst))
            done += 1
        except OSError as exc:
            failed += 1
            print(f"   FAILED {src}: {exc}")
    print(f"\nrenamed {done}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

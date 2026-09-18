#!/usr/bin/env python3
r"""
Account for every animation clip in the ripped tree that mentions a character.

`find` lists what the exporter will handle. This walks the *whole* tree instead and
classifies every folder holding .anim files whose path or filenames mention the ID,
so anything the exporter would miss shows up as NOT COVERED rather than staying
invisible. Clips are classified individually, because these folders mix real body
animation with timeline and material curves that drive no skeleton.

    python ag.py audit 1095
    python ag.py audit 1095 --verbose     # list every folder, not just the gaps
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clip_sets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cid", help="character id, e.g. 1095")
    ap.add_argument("--verbose", action="store_true", help="list every folder found")
    args = ap.parse_args()
    cid = args.cid

    covered_sets = clip_sets.main_sets(cid) + clip_sets.extra_sets(cid)
    covered = {(os.path.normcase(s["dir"]), c) for s in covered_sets for c in s["clips"]}
    print(f"exporter covers {len(covered)} clip(s) in {len(covered_sets)} set(s)\n")

    rows = []
    for dirpath, _, filenames in os.walk(clip_sets.ANIM_ROOT):
        anims = [f for f in filenames if f.endswith(".anim")]
        if not anims:
            continue
        named = [f for f in anims if cid in f]
        if cid not in dirpath and not named:
            continue
        pool = anims if cid in dirpath else named
        skeletal = [f for f in pool
                    if clip_sets.is_skeletal(os.path.join(dirpath, f))]
        if not skeletal and not args.verbose:
            continue
        missing = [f for f in skeletal
                   if (os.path.normcase(dirpath), f) not in covered]
        rows.append((os.path.relpath(dirpath, clip_sets.ANIM_ROOT),
                     len(pool), len(skeletal), missing))

    rows.sort()
    total_skel = sum(r[2] for r in rows)
    total_miss = sum(len(r[3]) for r in rows)

    if args.verbose:
        print(f"{'folder':<74} {'clips':>5} {'skeletal':>9}")
        for rel, n, skel, _ in rows:
            print(f"{rel:<74} {n:>5} {skel:>9}")
        print()

    print(f"skeletal clips mentioning {cid}: {total_skel}")
    print(f"  covered by the exporter    : {total_skel - total_miss}")
    print(f"  NOT covered                : {total_miss}")

    if total_miss:
        print("\nnot covered:")
        for rel, _, _, missing in rows:
            if missing:
                print(f"  {rel}  ({len(missing)})")
                print(f"      {', '.join(os.path.splitext(m)[0] for m in missing)}")
        print("\nIf these should be exported, add the root to EXTRA_ROOTS in "
              "agtools/clip_sets.py.")
    else:
        print("\nnothing missing.")

    # Team-up folders carry both participants; the partner's half is not this
    # character's animation and is expected to sit outside the count above.
    partners = sorted({os.path.basename(r[0]) for r in rows
                       if os.path.basename(r[0])[:6].isdigit()
                       and not os.path.basename(r[0]).startswith(cid)})
    if partners:
        print(f"\n({len(partners)} folder(s) belong to partner characters in team-up "
              f"ultimates and animate their rigs, not {cid}: {', '.join(partners[:8])}"
              f"{' ...' if len(partners) > 8 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

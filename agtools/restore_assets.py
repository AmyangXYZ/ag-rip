#!/usr/bin/env python3
"""
Restore original file names/paths for AetherGazer's StreamingAssets cache.

The game stores every asset as  <root>/<h[0]>/<h[1]>/<md5>.ys  and keeps the real
relative path in AssetHash_Info.bytes, a plain-JSON manifest whose "assetHashList"
holds rows of the form:

    "comeffect/effect_textures.ys|cbdfae6dfb816fcc62b5c7087a4758f3|541643587"
     ^ real relative path          ^ md5 of the stored file        ^ stored size

Unity bundles are additionally stored with 1-16 leading NUL bytes in front of the
"UnityFS" magic, which stops AssetRipper/AssetStudio from recognising them. This
script strips that padding. CRIWARE audio/video (.acb/.awb/.usm/.acf) carries no
padding and is copied verbatim.

Examples
--------
  # see what would happen, no files touched
  python restore_assets.py --dry-run

  # restore everything (needs ~51 GB free)
  python restore_assets.py

  # restore just the UI art, into a chosen folder
  python restore_assets.py --filter "assets/uiresources/*" --out D:\\AG_ui

  # save disk: reuse the originals where possible instead of copying
  python restore_assets.py --mode hardlink
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

UNITY_MAGIC = b"UnityFS\x00"
MAX_PADDING = 64          # observed 1-16; 64 is a safe ceiling
COPY_CHUNK = 4 * 1024 * 1024
HEAD_READ = 128


# --------------------------------------------------------------------------- #
# Windows long-path handling: dest paths can exceed the legacy 260 char limit.
# --------------------------------------------------------------------------- #
def longpath(path: str) -> str:
    if os.name != "nt":
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def find_manifest(root: str) -> str:
    preferred = os.path.join(root, "AssetHash_Info.bytes")
    if os.path.isfile(preferred):
        return preferred
    candidates = [
        os.path.join(root, n)
        for n in os.listdir(root)
        if n.lower().startswith("assethash") and n.lower().endswith(".bytes")
    ]
    if not candidates:
        raise SystemExit(
            f"No manifest found in {root}.\n"
            "Expected AssetHash_Info.bytes (or assethash_*.bytes)."
        )
    return max(candidates, key=os.path.getsize)


def load_manifest(path: str):
    with open(longpath(path), "rb") as fh:
        raw = fh.read()
    data = json.loads(raw.decode("utf-8-sig"))
    entries = []
    for row in data.get("assetHashList", []):
        parts = row.split("|")
        if len(parts) < 3:
            continue
        rel, md5, size = parts[0], parts[1].lower(), parts[2]
        entries.append((rel.replace("\\", "/"), md5, int(size)))
    return data, entries


def source_for(root: str, md5: str) -> str:
    return os.path.join(root, md5[0], md5[1], md5 + ".ys")


# --------------------------------------------------------------------------- #
# Padding detection
# --------------------------------------------------------------------------- #
def payload_offset(head: bytes) -> int:
    """Bytes of leading NUL padding to drop, or 0 if the file starts clean."""
    idx = head.find(UNITY_MAGIC)
    if idx <= 0 or idx > MAX_PADDING:
        return 0
    if any(head[:idx]):       # prefix isn't pure padding - leave it alone
        return 0
    return idx


def md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(longpath(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(COPY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Transfer
# --------------------------------------------------------------------------- #
def write_stripped(src: str, dst: str, offset: int) -> None:
    with open(longpath(src), "rb") as fi:
        if offset:
            fi.seek(offset)
        with open(longpath(dst), "wb") as fo:
            shutil.copyfileobj(fi, fo, COPY_CHUNK)


def place(src: str, dst: str, offset: int, mode: str) -> str:
    """Materialise dst from src. Returns the method actually used."""
    os.makedirs(os.path.dirname(longpath(dst)), exist_ok=True)

    if offset == 0 and mode == "hardlink":
        try:
            os.link(longpath(src), longpath(dst))
            return "hardlink"
        except OSError:
            pass                      # different volume / no permission -> copy
    if offset == 0 and mode == "move":
        try:
            os.replace(longpath(src), longpath(dst))
            return "move"
        except OSError:
            pass

    write_stripped(src, dst, offset)
    if mode == "move":
        try:
            os.remove(longpath(src))
        except OSError:
            pass
        return "move+strip" if offset else "move"
    return "copy+strip" if offset else "copy"


def clone(first_dst: str, dst: str, mode: str) -> str:
    """Second and later destinations that share one source hash."""
    os.makedirs(os.path.dirname(longpath(dst)), exist_ok=True)
    if mode in ("hardlink", "move"):
        try:
            os.link(longpath(first_dst), longpath(dst))
            return "hardlink"
        except OSError:
            pass
    shutil.copyfile(longpath(first_dst), longpath(dst))
    return "copy"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
GAME_CACHE = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data"
              r"\StreamingAssets\Windows")


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Rebuild real file names for AetherGazer StreamingAssets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples")[-1],
    )
    ap.add_argument("--root", default=GAME_CACHE,
                    help="folder holding the 0/..f hash tree "
                         "(default: the game's StreamingAssets/Windows)")
    ap.add_argument("--out", default=None,
                    help="output folder (default: <root>_restored)")
    ap.add_argument("--mode", choices=("copy", "hardlink", "move"), default="copy",
                    help="copy (safe, default) | hardlink (saves disk, same volume "
                         "only, cannot be used for padded bundles) | move (destructive)")
    ap.add_argument("--filter", action="append", default=[], metavar="GLOB",
                    help="only restore paths matching this glob; repeatable")
    ap.add_argument("--jobs", type=int, default=8, help="worker threads (default 8)")
    ap.add_argument("--no-strip", action="store_true",
                    help="keep the leading NUL padding (bundles stay unreadable)")
    ap.add_argument("--verify", action="store_true",
                    help="md5-check every source against the manifest (slow)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite destinations that already look complete")
    ap.add_argument("--rename-bundles", metavar="EXT", default=None,
                    help="give Unity bundles this extension instead, e.g. .bundle "
                         "(helps tools that filter by extension)")
    ap.add_argument("--dry-run", action="store_true", help="report only, touch nothing")
    ap.add_argument("--limit", type=int, default=0, help="stop after N entries (testing)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    out = os.path.abspath(args.out) if args.out else root.rstrip("\\/") + "_restored"
    if os.path.normcase(out).startswith(os.path.normcase(root) + os.sep):
        return err(f"--out ({out}) must not sit inside --root ({root}).")
    if args.mode == "move" and args.dry_run is False and not args.force:
        print("NOTE: --mode move deletes the originals as it goes.\n"
              "      Re-run with --force to confirm, or use --mode copy/hardlink.")
        return 2

    manifest_path = find_manifest(root)
    print(f"manifest : {manifest_path}")
    meta, entries = load_manifest(manifest_path)
    print(f"game     : {meta.get('versionName')} "
          f"(appVersion {meta.get('appVersion')}, build {meta.get('buildCode')})")
    print(f"entries  : {len(entries)}")

    if args.filter:
        pats = [p.replace("\\", "/").lower() for p in args.filter]
        entries = [e for e in entries
                   if any(fnmatch.fnmatch(e[0].lower(), p) for p in pats)]
        print(f"filtered : {len(entries)} match {args.filter}")
    if args.limit:
        entries = entries[: args.limit]

    # Group by hash so a source shared by several paths is read only once.
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for rel, md5, size in entries:
        groups[md5].append((rel, size))

    total_bytes = sum(size for _, md5, size in entries)
    print(f"payload  : {total_bytes / 1024**3:.2f} GiB")
    print(f"out      : {out}")
    print(f"mode     : {args.mode}"
          f"{' (dry run)' if args.dry_run else ''}"
          f"{', padding kept' if args.no_strip else ''}\n")

    rows: list[tuple] = []
    missing: list[tuple[str, str]] = []
    counts: defaultdict[str, int] = defaultdict(int)
    done_bytes = 0
    lock = threading.Lock()
    started = time.time()
    processed = 0

    def dest_for(rel: str, offset: int) -> str:
        if args.rename_bundles and offset and rel.lower().endswith(".ys"):
            rel = rel[:-3] + args.rename_bundles
        return os.path.join(out, rel.replace("/", os.sep))

    def handle(md5: str, targets: list[tuple[str, int]]):
        nonlocal done_bytes, processed
        src = source_for(root, md5)
        local: list[tuple] = []
        try:
            st = os.stat(longpath(src))
        except OSError:
            with lock:
                for rel, size in targets:
                    missing.append((rel, md5))
                    counts["missing"] += 1
                    local.append(("missing", md5, size, rel, 0, ""))
                rows.extend(local)
                processed += len(targets)
            return

        with open(longpath(src), "rb") as fh:
            head = fh.read(HEAD_READ)
        offset = 0 if args.no_strip else payload_offset(head)

        if args.verify:
            actual = md5_of(src)
            if actual != md5:
                with lock:
                    for rel, size in targets:
                        counts["corrupt"] += 1
                        local.append(("corrupt", md5, size, rel, offset, actual))
                    rows.extend(local)
                    processed += len(targets)
                return

        expected_out = st.st_size - offset
        first_dst = None
        for rel, size in targets:
            dst = dest_for(rel, offset)
            try:
                if (not args.force and os.path.exists(longpath(dst))
                        and os.path.getsize(longpath(dst)) == expected_out):
                    status, how = "skipped", "exists"
                elif args.dry_run:
                    status, how = "planned", args.mode
                elif first_dst is None:
                    how = place(src, dst, offset, args.mode)
                    status, first_dst = "ok", dst
                else:
                    how = clone(first_dst, dst, args.mode)
                    status = "ok"
                if status == "ok" and first_dst is None:
                    first_dst = dst
            except Exception as exc:                       # noqa: BLE001
                status, how = "error", f"{type(exc).__name__}: {exc}"
            local.append((status, md5, size, rel, offset, how))

        with lock:
            rows.extend(local)
            for status, _, size, _, _, _ in local:
                counts[status] += 1
                done_bytes += size
            processed += len(targets)
            if processed % 500 < len(targets) or processed == len(entries):
                elapsed = max(time.time() - started, 1e-6)
                pct = 100.0 * done_bytes / total_bytes if total_bytes else 100.0
                rate = done_bytes / 1024**2 / elapsed
                eta = (total_bytes - done_bytes) / (done_bytes / elapsed) if done_bytes else 0
                sys.stdout.write(
                    f"\r  {processed}/{len(entries)} files  {pct:5.1f}%  "
                    f"{rate:6.1f} MiB/s  eta {eta/60:5.1f} min   ")
                sys.stdout.flush()

    if not args.dry_run:
        os.makedirs(longpath(out), exist_ok=True)

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for md5, targets in groups.items():
            pool.submit(handle, md5, targets)

    print("\n\n--- summary ---")
    for key in ("ok", "planned", "skipped", "missing", "corrupt", "error"):
        if counts[key]:
            print(f"  {key:8}: {counts[key]}")
    padded = sum(1 for r in rows if r[4])
    print(f"  {'stripped':8}: {padded} bundles had NUL padding removed")
    print(f"  elapsed : {(time.time() - started) / 60:.1f} min")

    report_dir = out if not args.dry_run else here
    os.makedirs(longpath(report_dir), exist_ok=True)
    report = os.path.join(report_dir, "restore_report.csv")
    with open(longpath(report), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["status", "md5", "stored_size", "path", "padding", "detail"])
        w.writerows(sorted(rows, key=lambda r: (r[0], r[3])))
    print(f"\nreport   : {report}")

    if missing:
        mp = os.path.join(report_dir, "missing.txt")
        with open(longpath(mp), "w", encoding="utf-8") as fh:
            for rel, md5 in sorted(missing):
                fh.write(f"{md5}  {rel}\n")
        print(f"missing  : {mp}  ({len(missing)} entries)")
        print("           these are on-demand downloads the client never fetched.")

    if counts["error"] or counts["corrupt"]:
        return 1
    return 0


def err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

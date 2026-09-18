#!/usr/bin/env python3
r"""
Which bundles does a bundle need? - the dependency closure AssetRipper must be fed.

A bundle refers to other bundles only by their internal serialized-file name
(`archive:/CAB-<hash>/CAB-<hash>`), never by path, and the game's manifest that
would translate those is not shipped in readable form. So this builds the
translation itself:

  1. CAB index - for every .ys under Windows_restored, read just the UnityFS
     header and its (LZ4) directory block to list the CAB files it contains.
     63k bundles, only a few hundred bytes each, cached to AG_cache/cab_index.json.
  2. closure  - load a bundle with UnityPy, read each serialized file's
     `externals`, map them through the index, and repeat until nothing new.

Usage
-----
  python agtools/bundle_deps.py --index                  (re)build the CAB index
  python agtools/bundle_deps.py comscene/levels/x343.ys  print the closure
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time

ROOT = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data\StreamingAssets"
        r"\Windows_restored")
CACHE = r"C:\AetherGazerStarter\AG_cache\cab_index.json"
UNITY_VERSION = "2022.3.62f3"


# ---------------------------------------------------------------- UnityFS header
def _cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def _decompress(data: bytes, kind: int, size: int) -> bytes:
    if kind == 0:
        return data
    if kind in (2, 3):
        import lz4.block
        return lz4.block.decompress(data, uncompressed_size=size)
    if kind == 1:
        import lzma
        props, dict_size = data[0], struct.unpack("<I", data[1:5])[0]
        lc, rem = props % 9, props // 9
        lp, pb = rem % 5, rem // 5
        filt = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                 "lc": lc, "lp": lp, "pb": pb}]
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filt).decompress(data[5:])[:size]
    raise ValueError(f"unknown compression {kind}")


def cab_names(path: str) -> list[str]:
    """Names of the files inside one UnityFS bundle, from its directory block."""
    with open(path, "rb") as fh:
        head = fh.read(256)
        if not head.startswith(b"UnityFS\0"):
            return []
        pos = 8
        version = struct.unpack(">I", head[pos:pos + 4])[0]
        pos += 4
        _, pos = _cstr(head, pos)            # "5.x.x"
        _, pos = _cstr(head, pos)            # engine version (blanked here)
        _size, csize, usize, flags = struct.unpack(">qIII", head[pos:pos + 20])
        pos += 20
        if version >= 7:
            pos = (pos + 15) & ~15
        if flags & 0x80:                     # directory stored at the end
            fh.seek(-csize, os.SEEK_END)
        else:
            fh.seek(pos)
        block = _decompress(fh.read(csize), flags & 0x3F, usize)
    p = 16                                   # hash
    nblocks = struct.unpack(">i", block[p:p + 4])[0]
    p += 4 + nblocks * 10
    nnodes = struct.unpack(">i", block[p:p + 4])[0]
    p += 4
    out = []
    for _ in range(nnodes):
        p += 20                              # offset, size, flags
        name, p = _cstr(block, p)
        out.append(name)
    return out


def build_index() -> dict[str, str]:
    """CAB name (lower-case, no extension) -> bundle path relative to ROOT."""
    t0 = time.time()
    index: dict[str, str] = {}
    n = bad = 0
    for dp, _, fns in os.walk(ROOT):
        for fn in fns:
            if not fn.endswith(".ys"):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, ROOT)
            n += 1
            try:
                names = cab_names(full)
            except Exception:                                  # noqa: BLE001
                bad += 1
                continue
            for name in names:
                cab = name.split(".")[0].lower()
                if cab.startswith("cab-"):
                    index.setdefault(cab, rel)
            if n % 5000 == 0:
                print(f"  {n} bundles...", flush=True)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    print(f"{n} bundles, {len(index)} CAB files, {bad} unreadable, "
          f"{time.time() - t0:.0f}s -> {CACHE}")
    return index


def load_index() -> dict[str, str]:
    if not os.path.isfile(CACHE):
        return build_index()
    with open(CACHE, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------- closure
def externals(path: str) -> set[str]:
    """CAB names a bundle's serialized files point at."""
    import warnings
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    out = set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        env = UnityPy.load(path)
        for f in env.files.values():
            for sf in getattr(f, "files", {}).values():
                for e in getattr(sf, "externals", None) or []:
                    name = e.path.replace("\\", "/").rsplit("/", 1)[-1]
                    out.add(name.split(".")[0].lower())
    return out


def closure(bundles: list[str], index: dict[str, str] | None = None,
            verbose: bool = False) -> tuple[list[str], set[str]]:
    """Every bundle the given ones need, transitively (inputs included).

    Returns (bundle paths relative to ROOT, CAB names nothing in the tree provides).
    """
    index = index if index is not None else load_index()
    seen: set[str] = set()
    missing: set[str] = set()
    todo = [os.path.relpath(os.path.join(ROOT, b), ROOT) for b in bundles]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for cab in externals(os.path.join(ROOT, rel)):
            dep = index.get(cab)
            if dep is None:
                # Built-in resources ("unity default resources", "library/...")
                # are not CABs and are expected to be absent.
                if cab.startswith("cab-"):
                    missing.add(cab)
            elif dep not in seen:
                todo.append(dep)
        if verbose:
            print(f"  {len(seen)} bundles, {len(todo)} queued", end="\r", flush=True)
    if verbose:
        print()
    return sorted(seen), missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundles", nargs="*", help="paths relative to Windows_restored")
    ap.add_argument("--index", action="store_true", help="rebuild the CAB index")
    args = ap.parse_args()
    if args.index:
        build_index()
    if args.bundles:
        deps, missing = closure(args.bundles, verbose=True)
        total = sum(os.path.getsize(os.path.join(ROOT, d)) for d in deps)
        for d in deps:
            print(d)
        print(f"\n{len(deps)} bundle(s), {total / 1e6:.0f} MB; "
              f"{len(missing)} unresolved CAB reference(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

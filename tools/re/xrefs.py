"""Find which IL2CPP methods reference given string literals (or any rva).

    python tools/re/xrefs.py _AcceptLightProbe SimSceneTint ...
    python tools/re/xrefs.py --rva 0x4B185F0

String literals live in slots listed in AG_cache/re/dump/stringliteral.json; code
loads them RIP-relative. Every 4-byte disp32 in .text with rva(p)+4+disp == slot is
a candidate reference (checked for disp32-at-end and disp32+imm8/imm32 forms);
hits are mapped to the enclosing method by the method table from script.json.
"""
import bisect
import json
import os
import sys

import numpy as np
import pefile

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DLL = os.path.join(TOP, "AetherGazer", "GameAssembly.dll")
DUMP = os.path.join(TOP, "AG_cache", "re", "dump")
_cache = {}


def text():
    if "text" not in _cache:
        pe = pefile.PE(DLL, fast_load=True)
        # game code is in the "il2cpp" section, runtime/engine glue in ".text"
        _cache["text"] = [(s.VirtualAddress, np.frombuffer(s.get_data(), dtype=np.uint8))
                          for s in pe.sections if s.Characteristics & 0x20000000]
    return _cache["text"]


def methods():
    if "m" not in _cache:
        d = json.load(open(os.path.join(DUMP, "script.json"), encoding="utf-8"))
        ms = sorted((m["Address"], m["Name"]) for m in d["ScriptMethod"])
        _cache["m"] = ([a for a, _ in ms], [n for _, n in ms])
    return _cache["m"]


def literals():
    if "s" not in _cache:
        s = json.load(open(os.path.join(DUMP, "stringliteral.json"), encoding="utf-8"))
        _cache["s"] = {x["value"]: int(x["address"], 16) for x in s}
    return _cache["s"]


def refs(target_rva: int) -> list[int]:
    hits = []
    for va, raw in text():
        n = len(raw) - 8
        d = (raw[0:n].astype(np.int64) | (raw[1:n + 1].astype(np.int64) << 8) |
             (raw[2:n + 2].astype(np.int64) << 16) | (raw[3:n + 3].astype(np.int64) << 24))
        d = np.where(d >= 1 << 31, d - (1 << 32), d)
        pos = np.arange(n, dtype=np.int64) + va
        for tail in (4, 5, 8):          # disp32 at end, + imm8, + imm32
            hits += list(np.nonzero(pos + tail + d == target_rva)[0] + va)
    return sorted(set(int(h) for h in hits))


def owner(rva: int) -> str:
    addrs, names = methods()
    i = bisect.bisect_right(addrs, rva) - 1
    return f"{names[i]}+{rva - addrs[i]:#x} (@{addrs[i]:#x})" if i >= 0 else "?"


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--rva":
        targets = [(a, int(a, 16)) for a in args[1:]]
    else:
        lit = literals()
        targets = [(a, lit[a]) for a in args if a in lit]
        for a in args:
            if a not in lit:
                print(f"{a}: no such literal")
    for name, rva in targets:
        print(f"== {name} (slot {rva:#x})")
        for h in refs(rva):
            print(f"   {h:#x}  {owner(h)}")

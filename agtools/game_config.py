#!/usr/bin/env python3
r"""The game's own config tables, read from its Lua.

    python agtools/game_config.py voices 109502      -> sequence: voice lines

Game logic and configs are LuaJIT bytecode (FR2, stripped) in the "scripts64" bundle,
at assets/luabuilds/luajit2.0/x64/<module>.lua.bytes. A config module's body is a table
template, so its constants ARE the data; tools/re/ljdump.py reads them. The modules are
cached under AG_cache/luacfg/.

voices(skin): which voice line the game starts with each home-screen sequence.
  herointeractionconvertedcfg  per interaction type (mainTouch, idle, touch2, ...) two
                               parallel lists: talk names ("109502_touch_bed01_1_day") and
                               actions ("touch1_action1_3__day"); an action listed twice
                               has alternate takes the game picks between
  herointeractactioncfg        "<skin>_<action>" -> embed_list: the sequences (prefabs) it
                               plays in order ("idle2__day" -> idle2_1/2/3_action1_1)
The talk plays from the action's start, so it belongs to its first sequence.
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "AG_cache", "luacfg")
PREFIX = "assets/luabuilds/luajit2.0/x64/"


def module(name: str) -> bytes:
    """A Lua module's bytecode ("game/config/herointeractactioncfg")."""
    path = os.path.join(CACHE, f"{name}.lua.bytes")
    if not os.path.isfile(path):
        _extract("game/config/")
    return open(path, "rb").read()


def _extract(folder: str) -> None:
    sys.path.insert(0, HERE)
    import UnityPy
    import bundle_deps
    raw = open(os.path.join(bundle_deps.ROOT, "scripts64"), "rb").read()
    env = UnityPy.load(raw[raw.find(b"UnityFS"):])
    for path, obj in env.container.items():
        p = path.lower()
        if p.startswith(PREFIX + folder) and p.endswith(".lua.bytes"):
            out = os.path.join(CACHE, p[len(PREFIX):])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            data = obj.read()
            script = getattr(data, "m_Script", None)
            blob = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else bytes(script)
            open(out, "wb").write(blob)


def consts(name: str) -> list:
    """Every constant of a module, protos in order."""
    sys.path.insert(0, os.path.join(ROOT, "tools", "re"))
    import ljdump
    return [k for p in ljdump.protos(module(name)) for k in p["kgc"]]


def sequences(skin: str) -> dict[str, list[str]]:
    """action -> the sequences it plays, in order (herointeractactioncfg embed_list)."""
    k = consts("game/config/herointeractactioncfg")
    out = {}
    for i, v in enumerate(k):
        if isinstance(v, dict) and str(v.get("action", "")).startswith(f"{skin}_"):
            act = v["action"][len(skin) + 1:]
            embed = k[i - 1] if i >= 2 and isinstance(k[i - 1], list) and k[i - 2] == v["action"] else []
            out[act] = [e for e in embed if isinstance(e, str)]
    return out


def voices(skin: str) -> dict[str, list[str]]:
    """sequence -> the voice cues ("v_s_109502_touch_bed01_1_day") the game starts with it,
    alternate takes in config order."""
    k = consts("game/config/herointeractionconvertedcfg")
    seqs = sequences(skin)
    out: dict[str, list[str]] = {}
    for i, v in enumerate(k):
        if not (isinstance(v, dict) and str(v.get("skinId")) == skin and i >= 2):
            continue
        talks, actions = k[i - 2], k[i - 1]
        if not (isinstance(talks, list) and isinstance(actions, list) and len(talks) == len(actions)):
            continue
        for talk, act in zip(talks, actions):
            played = seqs.get(act) or []
            if not played:              # no embed list: the action names its one sequence
                night = seqs.get(act.replace("__day", "__night")) or []
                played = [act.replace("__day", "")] if not act.endswith("__day") else \
                    [s.replace("__night", "") for s in night[:1]]
            if played and talk:
                out.setdefault(played[0], []).append(f"v_s_{talk}")
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "voices":
        print(json.dumps(voices(sys.argv[2]), indent=1, ensure_ascii=False))
    else:
        print(__doc__)

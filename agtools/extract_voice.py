#!/usr/bin/env python3
r"""Voice / scene audio -> WAV, and the game's pre-analysed lip-sync data.

    python ag.py voice vo_sys_109501 ui_scene_109501 --lang zh,ja   -> AG_voice/<lang>/<sheet>/<cue>.wav

Voice banks are CRI ACB+AWB per language under StreamingAssets/Voice/<lang>/, stored by
MD5 like the bundles; Voice/<lang>/voice_hash_<lang>_<version>.bytes (JSON) maps
"<sheet>.acb|<md5>|<size>". A language's files exist only if the game downloaded that
voice pack (this install: zh complete, ja tables only). Non-voice banks (ui_scene_<skin>:
the DLC interactions' music/SFX, c_<skin>) are plain .acb in Windows_restored/.
Audio is HCA with the game's key (Aether Gazer, in vgmstream's key list - tools/vgmstream).

Lip sync: CriLipsExPlayer plays per-cue mouth data pre-analysed by CRI LipSync, shipped
per language in crilipsexdata/<lang>.ys (TextAsset <sheet>.bytes):
    int32 cues; per cue: int32 len, name, int32 length_ms, int32 frames,
    frames x (uint16 index, A, I, U, E, O)          30 fps (length_ms / frames = 33.3), 0..1000
CriLipsExPlayer (decompiled) shows frame (playback ms / 33) with weight raw/10 on the
mouth's A/U/E/O blend shapes (Mouth_a/u/e/o); its I index is -1, so "i" is never shown.
The /33 drifts 1% ahead of the voice; exports sample the data at its true 30 fps.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import struct
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SA = os.path.join(ROOT, "AetherGazer", "AetherGazer_Data", "StreamingAssets")
VGM = os.path.join(ROOT, "tools", "vgmstream", "vgmstream-cli.exe")
OUT = os.path.join(ROOT, "AG_voice")
CACHE = os.path.join(ROOT, "AG_cache", "audio")


def voice_files(sheet: str, lang: str) -> tuple[str | None, str | None]:
    """(acb, awb) of a voice bank in the local install, or None where not downloaded."""
    tables = sorted(glob.glob(os.path.join(SA, "Voice", lang, f"voice_hash_{lang}_*.bytes")))
    found = {}
    for table in reversed(tables):                       # newest first
        for entry in json.load(open(table, encoding="utf-8"))["assetHashList"]:
            name, md5, _ = entry.split("|")
            if name in (f"{sheet}.acb", f"{sheet}.awb") and name not in found:
                p = os.path.join(SA, "Voice", lang, md5[0], md5[1], md5 + ".ys")
                found[name] = p if os.path.isfile(p) else None
        if len(found) == 2:
            break
    return found.get(f"{sheet}.acb"), found.get(f"{sheet}.awb")


def decode(sheet: str, lang: str | None, out_root: str = OUT) -> dict[str, str]:
    """Decode every cue of a bank to <out>/<lang>/<sheet>/<cue>.wav. Returns cue -> wav."""
    work = os.path.join(CACHE, lang or "common")
    os.makedirs(work, exist_ok=True)
    if lang:
        acb, awb = voice_files(sheet, lang)
        if not acb:
            return {}
        shutil.copyfile(acb, os.path.join(work, f"{sheet}.acb"))
        if awb:
            shutil.copyfile(awb, os.path.join(work, f"{sheet}.awb"))
        src = os.path.join(work, f"{sheet}.awb" if awb else f"{sheet}.acb")
    else:
        acb = os.path.join(SA, "Windows_restored", f"{sheet}.acb")
        if not os.path.isfile(acb):
            return {}
        shutil.copyfile(acb, os.path.join(work, f"{sheet}.acb"))
        src = os.path.join(work, f"{sheet}.acb")
    out = os.path.join(out_root, lang or "common", sheet)
    os.makedirs(out, exist_ok=True)
    r = subprocess.run([VGM, "-S", "0", "-o", os.path.join(out, "?n.wav"), src], capture_output=True, text=True)
    if r.returncode:
        print(f"  ! vgmstream {sheet} ({lang}): {r.stderr.strip()[:200]}")
    return {os.path.splitext(f)[0]: os.path.join(out, f) for f in os.listdir(out) if f.endswith(".wav")}


_lips_cache: dict[str, dict] = {}


def lips(sheet: str, lang: str) -> dict[str, dict]:
    """cue -> {"length_ms", "fps": 30, "aiueo": [[a,i,u,e,o] 0..1 per frame]}."""
    key = f"{lang}/{sheet}"
    if key in _lips_cache:
        return _lips_cache[key]
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
    bundle = os.path.join(SA, "Windows_restored", "crilipsexdata", f"{lang}.ys")
    data = None
    if os.path.isfile(bundle):
        for obj in UnityPy.load(bundle).objects:
            if obj.type.name == "TextAsset" and obj.peek_name() == sheet:
                t = obj.read_typetree()
                s = t["m_Script"]
                data = s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s)
                break
    out = {}
    if data:
        p = 0
        (n,) = struct.unpack_from("<i", data, p)
        p += 4
        for _ in range(n):
            (ln,) = struct.unpack_from("<i", data, p)
            p += 4
            name = data[p:p + ln].decode("utf-8")
            p += ln
            ms, fc = struct.unpack_from("<ii", data, p)
            p += 8
            frames = [struct.unpack_from("<6H", data, p + 12 * i) for i in range(fc)]
            p += 12 * fc
            if fc:
                out[name] = {"length_ms": ms, "fps": 30, "aiueo": [[v / 1000.0 for v in f[1:]] for f in frames]}
    _lips_cache[key] = out
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheets", nargs="+", help="cue sheets, e.g. vo_sys_109501 ui_scene_109501")
    ap.add_argument("--lang", default="zh,ja", help="voice languages (voice banks only)")
    args = ap.parse_args()
    for sheet in args.sheets:
        if sheet.startswith("vo_") or sheet.startswith("story_v"):
            for lang in args.lang.split(","):
                cues = decode(sheet, lang)
                print(f"{sheet} [{lang}]: " + (", ".join(sorted(cues)) if cues else "not in this install"))
                lp = lips(sheet, lang)
                if lp:
                    print(f"   lips: {', '.join(f'{k} ({len(v['aiueo'])}f)' for k, v in lp.items())}")
        else:
            cues = decode(sheet, None)
            print(f"{sheet}: " + (", ".join(sorted(cues)) if cues else "not found"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

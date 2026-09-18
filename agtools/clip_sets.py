#!/usr/bin/env python3
r"""
Where a character's animation clips actually live.

Most sit in the obvious place - ComChar/ArtResources/Char/Hero/<id>/<id><costume>/ -
but not all of them. Costume DLC poses hide under ComEffect/.../EffectChar/Hero/<id>/,
and plenty of other folders across the ripped tree carry the character's ID while
holding no skeletal animation at all:

  CooperateUniqueSkill/<a>_<b>/   team-up ultimates: float/timeline curves, 0 bone paths
  Effect/Animations/<id>/         VFX transforms on a single effect object
  Oath/FaceTimeline/<id>/         facial float curves, zero duration
  BattleUI/.../Ani/<id>/          UI widget animation

Those are real animation data but they don't drive a character skeleton, so there is
nothing to bind them to. This module finds the extra clip folders that DO drive one,
by looking at what a clip actually animates rather than trusting its location.
"""

from __future__ import annotations

import os
import re

ANIM_ROOT = r"C:\AetherGazerStarter\AG_animations"
HERO_ROOT = os.path.join(ANIM_ROOT, "ComChar", "ArtResources", "Char", "Hero")

# Roots outside the main hero tree that can still hold body animation.
#
#   under_cid    everything beneath <root>/<id>/ belongs to this character
#   costume_dirs <root>/<anything>/<id><costume>/ - team-up ultimates live in a
#                folder named for BOTH characters ("1095_1111") with one subfolder
#                per participant's costume, so only the subfolders whose own name
#                is this character's 6-digit costume id are ours; the rest animate
#                the partner's rig.
EXTRA_ROOTS = [
    (os.path.join(ANIM_ROOT, "ComEffect", "ArtResources", "EffectChar", "Hero"),
     "under_cid"),
    (os.path.join(ANIM_ROOT, "ComEffect", "ArtResources", "CooperateUniqueSkill"),
     "costume_dirs"),
]

# A clip is skeletal if it animates the Biped rig. Reading a bounded prefix is enough:
# 'root/Bip001' is among the first transform paths written.
_PEEK_BYTES = 4 << 20


def is_skeletal(anim_path: str) -> bool:
    try:
        with open(anim_path, "rb") as fh:
            return b"Bip001" in fh.read(_PEEK_BYTES)
    except OSError:
        return False


def costume_of(path: str, cid: str) -> str | None:
    """The <id><costume> the folder belongs to, read off its path segments."""
    for part in reversed(path.replace("/", os.sep).split(os.sep)):
        for token in part.replace("-", "_").split("_"):
            if token.startswith(cid) and len(token) >= len(cid) + 2 and token[:6].isdigit():
                return token[:6]
        if part.startswith(cid) and len(part) >= len(cid) + 2 and part[:6].isdigit():
            return part[:6]
    return None


def _candidate_dirs(only_cid: str | None = None):
    """Directories under the extra roots, tagged with the character they belong to.

    Pass `only_cid` to skip everything else before the (comparatively expensive)
    skeletal check runs - walking directory names is cheap, reading clips is not.
    """
    for root, scope in EXTRA_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            name = os.path.basename(dirpath)
            if scope == "under_cid":
                rel = os.path.relpath(dirpath, root).split(os.sep)
                if not rel or not rel[0].isdigit():
                    continue
                cid = rel[0]
            elif scope == "costume_dirs":
                if not (len(name) == 6 and name.isdigit()):
                    continue
                cid = name[:4]
            else:
                continue
            if only_cid and cid != only_cid:
                continue
            yield cid, dirpath, filenames


def extra_sets(cid: str) -> list[dict]:
    """Skeletal clip folders for `cid` outside the main hero tree.

    Each entry: {label, dir, clips, costume, suffix}. Clips are filtered file by
    file, not by sampling the folder: these folders freely mix real body animation
    ("combo_skill") with timeline and material curves that animate no skeleton at
    all ("Recorded (3)"), and which sorts first is pure luck.
    """
    return [entry for _, entry in _extra_entries(cid)]


def all_extra_sets() -> dict[str, list[dict]]:
    """Same, for every character at once - one walk instead of 131."""
    out: dict[str, list[dict]] = {}
    for cid, entry in _extra_entries(None):
        out.setdefault(cid, []).append(entry)
    return out


def _extra_entries(only_cid: str | None):
    for cid, dirpath, filenames in _candidate_dirs(only_cid):
        anims = sorted(f for f in filenames if f.endswith(".anim"))
        clips = [f for f in anims if is_skeletal(os.path.join(dirpath, f))]
        if clips:
            yield cid, {
                "label": os.path.relpath(dirpath, ANIM_ROOT),
                "dir": dirpath,
                "clips": clips,
                "costume": costume_of(dirpath, cid) or f"{cid}00",
                # These folders are named for what they are ("109503_pose"), not with
                # the ui/display suffix the hero tree uses, so they take the costume's
                # base model rather than having a suffix parsed out of the name.
                "suffix": "",
                "extra": True,
                "context": context_of(dirpath),
            }


def context_of(dirpath: str) -> str:
    """A short tag distinguishing one extra set from another, for filenames.

    Sets outside the hero tree reuse clip names freely: three team-ups each ship a
    "combo_skill" for the same costume, and 109502act/109502dlc both hold
    "action1_1". The set's own folder is what separates them ("act", "dlc", "main",
    "pose"), so strip the costume id off it; team-up folders are named for the
    costume alone, leaving nothing, so those fall back to the pairing above them.
    """
    own = re.sub(r"^\d{6}[_\-]?", "", os.path.basename(dirpath)).strip("_-")
    return own or os.path.basename(os.path.dirname(dirpath))


def main_sets(cid: str) -> list[dict]:
    """The character's own clip folders: <id><costume>[suffix]."""
    cdir = os.path.join(HERO_ROOT, cid)
    out = []
    if not os.path.isdir(cdir):
        return out
    for folder in sorted(os.listdir(cdir)):
        fdir = os.path.join(cdir, folder)
        if not (os.path.isdir(fdir) and folder.startswith(cid)):
            continue
        clips = sorted(f for f in os.listdir(fdir) if f.endswith(".anim"))
        if clips:
            out.append({"label": folder, "dir": fdir, "clips": clips,
                        "costume": folder[:6], "suffix": folder[len(cid) + 2:]})
    return out

#!/usr/bin/env python3
"""Read AnimationClips straight from a bundle (UnityPy), curves paired with their bindings.

    python agtools/unity_clip.py <bundle> [clip name]

AssetRipper's YAML export names curves from its own path/attribute resolution; curves it
cannot name (blend-shape curves of clips that live outside the model's bundle, e.g. the
home-screen facial clips) get identical names and are merged - the data is lost. The
compiled clip keeps everything: m_MuscleClip holds streamed, dense and constant curves,
and m_ClipBindingConstant.genericBindings lists, in the same order, what each drives
(path = CRC32 of the transform path, attribute = CRC32 of the property - for blend shapes
the channel name, e.g. CRC32("Eye_Close")).
"""
from __future__ import annotations

import math
import os
import struct
import sys
import warnings

warnings.filterwarnings("ignore")

UNITY_VERSION = "2022.3.62f3"


def _dims(binding: dict) -> int:
    """How many curves a generic binding consumes (transform bindings are vectors)."""
    if binding["typeID"] == 4:
        return {1: 3, 2: 4, 3: 3, 4: 3}.get(binding["attribute"], 1)
    return 1


class RawClip:
    def __init__(self, name: str, typetree: dict) -> None:
        self.name = name
        m = typetree["m_MuscleClip"]
        c = m["m_Clip"]["data"] if "data" in m["m_Clip"] else m["m_Clip"]
        self.start, self.stop = float(m["m_StartTime"]), float(m["m_StopTime"])
        self.sample_rate = float(typetree["m_SampleRate"])
        self.bindings = typetree["m_ClipBindingConstant"]["genericBindings"]
        # streamed: per curve a list of (time, c0, c1, c2, c3) segments
        sc = c["m_StreamedClip"]
        raw = struct.pack(f"<{len(sc['data'])}I", *sc["data"])
        self.n_streamed = sc["curveCount"]
        self._streamed = [[] for _ in range(self.n_streamed)]
        p = 0
        while p + 8 <= len(raw):
            t, nkeys = struct.unpack_from("<fi", raw, p)
            p += 8
            for _ in range(nkeys):
                idx, c0, c1, c2, c3 = struct.unpack_from("<i4f", raw, p)
                p += 20
                if 0 <= idx < self.n_streamed and math.isfinite(t):
                    self._streamed[idx].append((t, c0, c1, c2, c3))
        for keys in self._streamed:
            keys.sort(key=lambda k: k[0])
        dc = c["m_DenseClip"]
        self.n_dense = dc["m_CurveCount"]
        self._dense = (dc["m_BeginTime"], dc["m_SampleRate"], dc["m_FrameCount"], dc["m_SampleArray"])
        self._const = list(c["m_ConstantClip"]["data"])
        # curve index of each binding
        self.curve_of = []
        i = 0
        for b in self.bindings:
            self.curve_of.append(i)
            i += _dims(b)

    def curve(self, index: int, t: float) -> float:
        if index < self.n_streamed:
            keys = self._streamed[index]
            if not keys:
                return 0.0
            if t <= keys[0][0]:
                return keys[0][4]
            lo = 0
            for k in range(len(keys)):
                if keys[k][0] <= t:
                    lo = k
                else:
                    break
            tk, c0, c1, c2, c3 = keys[lo]
            d = t - tk
            return ((c0 * d + c1) * d + c2) * d + c3
        index -= self.n_streamed
        if index < self.n_dense:
            begin, rate, frames, arr = self._dense
            f = min(max((t - begin) * rate, 0.0), frames - 1)
            f0 = int(f)
            f1 = min(f0 + 1, frames - 1)
            a, b = arr[f0 * self.n_dense + index], arr[f1 * self.n_dense + index]
            return a + (b - a) * (f - f0)
        return self._const[index - self.n_dense]

    def value(self, binding_index: int, t: float) -> tuple[float, ...]:
        start = self.curve_of[binding_index]
        return tuple(self.curve(start + k, t) for k in range(_dims(self.bindings[binding_index])))


def load_clips(bundle: str, names: set[str] | None = None) -> list[RawClip]:
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    env = UnityPy.load(bundle)
    out = []
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        name = obj.peek_name()
        if names and name not in names:
            continue
        out.append(RawClip(name, obj.read_typetree()))
    return out


def main() -> int:
    clips = load_clips(sys.argv[1], {sys.argv[2]} if len(sys.argv) > 2 else None)
    for c in clips:
        kinds = {}
        for b in c.bindings:
            kinds[b["typeID"]] = kinds.get(b["typeID"], 0) + 1
        print(f"{c.name}: {c.stop - c.start:.3f}s, {len(c.bindings)} bindings {kinds}, "
              f"curves streamed {c.n_streamed} / dense {c.n_dense} / constant {len(c._const)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

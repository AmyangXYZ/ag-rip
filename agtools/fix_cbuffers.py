#!/usr/bin/env python3
r"""
Re-create the Forward+ constant buffers in the decompiled shaders (AG_shaders).

USCSandbox writes custom cbuffers as commented markers with the members as plain
globals ("// CBUFFER_START(_PlusLighting_TileBuffer) // 6"). That is fine for small ones,
but the Forward+ bin/tile tables (_PlusLighting_ZBins float4[1024], _PlusLighting_Tiles
float4[4096]) are 80 KB: as globals they overflow $Globals, so no PLUS_LIGHTING variant
compiles, and the game binds them as whole buffers (CommandBuffer.SetGlobalConstantBuffer,
PlusLightingFeature.SetupRenderFeature). Each becomes a real, guarded cbuffer again.

    python agtools/fix_cbuffers.py [AG_shaders dir]
"""
import os
import re
import sys

NAMES = ("_PlusLighting_ZBinBuffer", "_PlusLighting_TileBuffer")
PAT = re.compile(r"^([ \t]*)// CBUFFER_START\((" + "|".join(NAMES) + r")\) // \d+\n(.*?)^[ \t]*// CBUFFER_END[^\n]*\n",
                 re.M | re.S)


def fix(text: str) -> tuple[str, int]:
    def repl(m):
        ind, name, body = m.groups()
        return (f"{ind}#ifndef AG_CB_{name}\n{ind}#define AG_CB_{name}\n{ind}CBUFFER_START({name})\n"
                f"{body}{ind}CBUFFER_END\n{ind}#endif\n")
    return PAT.subn(repl, text)


def main() -> int:
    return fix_dir(sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AG_shaders"))


def fix_dir(root: str) -> int:
    files = n = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if not fn.endswith(".shader"):
                continue
            p = os.path.join(dp, fn)
            with open(p, encoding="utf-8") as fh:
                t = fh.read()
            t2, k = fix(t)
            if k:
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(t2)
                files += 1
                n += k
    print(f"{n} cbuffer blocks restored in {files} shaders")
    return 0


if __name__ == "__main__":
    sys.exit(main())

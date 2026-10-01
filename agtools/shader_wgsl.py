#!/usr/bin/env python3
r"""A game shader variant -> WGSL (vertex + fragment) with a binding map, mechanically.

    python agtools/shader_wgsl.py <Shader.shader> --keywords OPAQUE,MAIN_LIGHT_SHADOWS,... \
        [--pass 0] --out <dir>/<name>

writes <name>.wgsl (both entry points, `vert` and `frag`, one module) and <name>.json
(bindings: every uniform block's members with WGSL offsets, textures, samplers; vertex
inputs and the interpolants).

The decompiled shaders (AG_shaders, bit-exact from the game's D3D11 bytecode) hold
every keyword variant as `#if A && B // :DX11VertexSM40` / `...PixelSM40` blocks. A
variant compiled for a subset of keywords serves every superset the stage does not
care about, so each stage takes the block whose keywords are the LARGEST subset of the
requested set. The block becomes standalone HLSL (legacy samplers -> Texture/Sampler,
Unity built-ins declared, cbuffer scalar arrays as float4[N] read through .x - the same
16-byte-per-element memory HLSL gives them), then dxc -> SPIR-V -> naga -> WGSL. The
math is the game's own, instruction for instruction; nothing is ported by hand.

Tools: dxc (DirectXShaderCompiler release; --dxc or $DXC) and naga (cargo install naga-cli).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# "#if A && B // :DX11VertexSM40", "#elif A // ForwardBase:DX11PixelSM40", "#else // :DX11..."
HEADER = re.compile(r"^\s*#(if|elif|else)\b\s*(.*?)\s*//\s*[^:\n]*:DX11(Vertex|Pixel)SM\d+\s*$")
ANY_HEADER = re.compile(r"^\s*#(if|elif|else)\b.*//\s*[^:\n]*:DX11")

# Unity's built-in per-draw and per-frame values, declared once; the page fills them.
PRELUDE = """#define CBUFFER_START(n) cbuffer n {
#define CBUFFER_END };
#define UNITY_DECLARE_SHADOWMAP(n) Texture2D n; SamplerComparisonState sampler##n
#define UNITY_SAMPLE_SHADOW(n, c) n.SampleCmpLevelZero(sampler##n, (c).xy, (c).z)
#define UNITY_MATRIX_P glstate_matrix_projection
#define UNITY_MATRIX_V unity_MatrixV
#define UNITY_MATRIX_I_V unity_MatrixInvV
#define UNITY_MATRIX_VP unity_MatrixVP
#define UNITY_MATRIX_M unity_ObjectToWorld
cbuffer UnityPerDraw {
  float4x4 unity_ObjectToWorld;
  float4x4 unity_WorldToObject;
  float4 unity_WorldTransformParams;
  float4 unity_LightData;
  float4 unity_LightIndices[2];
  float4 unity_RenderingLayer;
  float4 unity_LODFade;
};
cbuffer UnityPerFrame {
  float3 _WorldSpaceCameraPos;
  float4x4 unity_MatrixV;
  float4x4 unity_MatrixInvV;
  float4x4 unity_MatrixVP;
  float4x4 unity_MatrixP;
  float4x4 glstate_matrix_projection;
  float4 _ProjectionParams;
  float4 _ScreenParams;
  float4 _ZBufferParams;
  float4 unity_OrthoParams;
  float4 _Time;
  float4 _SinTime;
  float4 _CosTime;
  float4 unity_DeltaTime;
};
"""


def passes(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges of each Pass { ... }."""
    starts = [i for i, l in enumerate(lines) if re.match(r"^\s*Pass\s*\{", l)]
    return [(s, (starts[k + 1] if k + 1 < len(starts) else len(lines))) for k, s in enumerate(starts)]


def blocks(lines: list[str], lo: int, hi: int) -> list[dict]:
    """Every variant block in a pass: stage, keyword set, line range.

    A chain is "#if A // :DX11VertexSM40", "#elif B // :DX11VertexSM40", ... and may close
    with a bare "#else" - the variant with none of the chain's keywords - which carries no
    stage comment and so takes its chain's stage."""
    heads = []
    stage = None
    for i in range(lo, hi):
        m = HEADER.match(lines[i])
        if m:
            stage = "vertex" if m.group(3) == "Vertex" else "pixel"
            kws = frozenset() if m.group(1) == "else" else frozenset(k.strip() for k in m.group(2).split("&&") if k.strip())
            heads.append((i, stage, kws))
        elif stage and re.match(r"^\s*#else\s*$", lines[i]):
            heads.append((i, stage, frozenset()))
    if not heads:
        # a single-variant pass: one program for both stages, CGPROGRAM .. ENDCG
        body = [i for i in range(lo, hi) if re.match(r"^\s*(CGPROGRAM|HLSLPROGRAM|ENDCG|ENDHLSL)\b", lines[i])]
        if len(body) >= 2:
            return [{"stage": st, "keywords": frozenset(), "start": body[0] + 1, "end": body[1]} for st in ("vertex", "pixel")]
    out = []
    for k, (i, st, kws) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else hi
        out.append({"stage": st, "keywords": kws, "start": i + 1, "end": end})
    return out


def balanced(text: str) -> str:
    """A block runs to the next variant header or, the last of its chain, to the chain's
    #endif - the first one that closes nothing - where it is cut."""
    depth, keep = 0, []
    for line in text.split("\n"):
        t = line.strip()
        if t.startswith(("#if", "#ifdef", "#ifndef")):
            depth += 1
        elif t.startswith("#endif"):
            if depth == 0:
                break                   # the chain's own #endif: the block ends here
            depth -= 1
        keep.append(line)
    return "\n".join(keep)


def pick(bl: list[dict], stage: str, want: set[str]) -> dict:
    cands = [b for b in bl if b["stage"] == stage and b["keywords"] <= want]
    if not cands:
        raise SystemExit(f"no {stage} variant within {sorted(want)}")
    best = max(len(b["keywords"]) for b in cands)
    top = [b for b in cands if len(b["keywords"]) == best]
    if len(top) > 1:
        raise SystemExit(f"ambiguous {stage} variants: {[sorted(b['keywords']) for b in top]}")
    return top[0]


def _calls(s: str, fn: str, emit) -> str:
    """Rewrite fn(a, b, ...) calls, parentheses balanced."""
    out, i = [], 0
    pat = re.compile(r"\b" + fn + r"\(")
    while True:
        m = pat.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:m.start()])
        j, depth, args, cur = m.end(), 1, [], ""
        while True:
            c = s[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            if c == "," and depth == 1:
                args.append(cur)
                cur = ""
            else:
                cur += c
            j += 1
        args.append(cur)
        out.append(emit([a.strip() for a in args]))
        i = j + 1


def standalone(body: str) -> str:
    """A decompiled block's declarations + function as HLSL dxc accepts."""
    def decl(m):
        t = "TextureCube" if m.group(1) == "samplerCUBE" else ("Texture3D" if m.group(1) == "sampler3D" else "Texture2D")
        return f"{t}<float4> {m.group(2)}; SamplerState sampler{m.group(2)};"

    body = re.sub(r"\b(sampler2D|samplerCUBE|sampler3D)\s+(\w+)\s*;", decl, body)
    body = _calls(body, "tex2Dlod", lambda a: f"{a[0]}.SampleLevel(sampler{a[0]}, ({a[1]}).xy, ({a[1]}).w)")
    body = _calls(body, "texCUBElod", lambda a: f"{a[0]}.SampleLevel(sampler{a[0]}, ({a[1]}).xyz, ({a[1]}).w)")
    body = _calls(body, "tex3Dlod", lambda a: f"{a[0]}.SampleLevel(sampler{a[0]}, ({a[1]}).xyz, ({a[1]}).w)")
    body = _calls(body, "tex2Dbias", lambda a: f"{a[0]}.SampleBias(sampler{a[0]}, ({a[1]}).xy, ({a[1]}).w)")
    body = _calls(body, "tex2Dgrad", lambda a: f"{a[0]}.SampleGrad(sampler{a[0]}, {a[1]}, {a[2]}, {a[3]})")
    body = _calls(body, "texCUBE", lambda a: f"{a[0]}.Sample(sampler{a[0]}, {a[1]})")
    body = _calls(body, "tex3D", lambda a: f"{a[0]}.Sample(sampler{a[0]}, {a[1]})")
    body = _calls(body, "tex2D", lambda a: f"{a[0]}.Sample(sampler{a[0]}, {a[1]})")
    # The front-face input: "uint facing: VFACE" is D3D11's SV_IsFrontFace as the bytecode
    # holds it (all ones for front, zero for back; "float" = +1/-1). As written, dxc would
    # take VFACE for an interpolant; the system value is declared and the old value rebuilt.
    def facing(m):
        typ, name = m.group(2), m.group(3)
        value = f"({name}_ff ? 0xFFFFFFFFu : 0u)" if typ == "uint" else f"({name}_ff ? 1.0 : -1.0)"
        return f"{m.group(1)}bool {name}_ff : SV_IsFrontFace{m.group(4)}\n    {typ} {name} = {value};"
    body = re.sub(r"(\bfrag\s*\([^)]*?)\b(uint|float)\s+(\w+)\s*:\s*VFACE\b([^)]*\)\s*\{)", facing, body)
    # HLSL cbuffer scalar arrays: one element per 16-byte register -> float4[N], read .x
    for name in re.findall(r"^\s*(?:float|uint|int)\s+(\w+)\[\d+\]\s*;", body, re.M):
        body = re.sub(r"^(\s*)(float|uint|int)(\s+" + name + r"\[\d+\]\s*;)",
                      lambda m: m.group(1) + m.group(2) + "4" + m.group(3), body, flags=re.M)
        body = re.sub(r"\b" + name + r"\[([^\]]+)\](?!\s*;)", lambda m, n=name: n + "[" + m.group(1) + "].x", body)
    # Loose uniform members as 4-vectors, read through the swizzle of their real width: HLSL
    # packs a float2 at a 4-byte offset inside a 16-byte register, which WGSL's uniform
    # layout cannot say. Same values, same math; only the declaration widens.
    WIDTH = {"float": ".x", "float2": ".xy", "float3": ".xyz", "half": ".x", "half2": ".xy", "half3": ".xyz",
             "int": ".x", "int2": ".xy", "int3": ".xyz", "uint": ".x", "uint2": ".xy", "uint3": ".xyz"}
    for typ, name in re.findall(r"^(float|float2|float3|half|half2|half3|int|int2|int3|uint|uint2|uint3)\s+(\w+)\s*;", body, re.M):
        base = "float" if typ.startswith(("float", "half")) else typ.rstrip("23")
        body = re.sub(r"^" + typ + r"(\s+" + name + r"\s*;)", base + r"4\1", body, flags=re.M)
        body = re.sub(r"(?<![.\w])" + name + r"\b(?!\s*;)(?!\s*\[)", "(" + name + WIDTH[typ] + ")", body)
    # the built-ins are declared by the prelude; the block may re-declare some
    for b in re.findall(r"^\s*(?:float4x4|float4|float3)\s+(\w+)(?:\[\d+\])?\s*;", PRELUDE, re.M):
        body = re.sub(r"^[ \t]*(?:float4x4|float4|float3|float)\s+" + b + r"(?:\[\d+\])?\s*;[^\n]*$", "", body, flags=re.M)
    return body


def structs(text: str) -> str:
    """appdata / v2f / fout struct declarations from a block."""
    return "\n".join(m.group(0) for m in re.finditer(r"struct\s+\w+\s*\{.*?\};", text, re.S))


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{os.path.basename(cmd[0])} failed:\n{(r.stderr or r.stdout)[-4000:]}")


# ---- WGSL layout of naga's output (uniform address space rules)
SCALAR = {"f32": (4, 4), "i32": (4, 4), "u32": (4, 4)}


def _size_align(t: str, structs_: dict) -> tuple[int, int]:
    t = t.strip()
    if t in SCALAR:
        return SCALAR[t]
    m = re.fullmatch(r"vec([234])<(\w+)>", t)
    if m:
        n = int(m.group(1))
        return (4 * n, 8 if n == 2 else 16)
    m = re.fullmatch(r"mat([234])x([234])<f32>", t)
    if m:
        c, r = int(m.group(1)), int(m.group(2))
        col = 8 if r == 2 else 16
        return (c * col, col)
    m = re.fullmatch(r"array<(.+),\s*(\d+)>", t)
    if m:
        es, ea = _size_align(m.group(1), structs_)
        stride = max(16, -(-es // ea) * ea)
        return (stride * int(m.group(2)), max(16, ea))
    if t in structs_:
        return structs_[t]["size"], structs_[t]["align"]
    raise ValueError(f"layout: unknown type {t}")


def wgsl_layout(wgsl: str) -> dict:
    structs_ = {}
    for m in re.finditer(r"struct\s+(\w+)\s*\{(.*?)\n\}", wgsl, re.S):
        members, off, align = [], 0, 1
        for line in m.group(2).strip().splitlines():
            mm = re.match(r"\s*(?:@\w+\([^)]*\)\s*)*(\w+):\s*(.+?),?\s*$", line)
            if not mm:
                continue
            size, a = _size_align(mm.group(2), structs_)
            off = -(-off // a) * a
            members.append({"name": mm.group(1), "type": mm.group(2), "offset": off, "size": size})
            off += size
            align = max(align, a)
        structs_[m.group(1)] = {"members": members, "size": -(-off // align) * align, "align": align}
    return structs_


def bindings(wgsl: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@group\((\d+)\)\s*@binding\((\d+)\)\s*\n?\s*var(?:<(\w+)>)?\s+(\w+):\s*([^;]+);", wgsl):
        out.append({"group": int(m.group(1)), "binding": int(m.group(2)), "space": m.group(3) or "handle",
                    "name": m.group(4), "type": m.group(5).strip()})
    return out


def translate(shader: str, keywords: set[str], pass_index: int, out: str, dxc: str,
              texture_space: bool = False) -> dict:
    """texture_space: a pass drawn image to image (the post chain). naga turns Vulkan's clip
    space round to WebGPU's by negating y, which flips such a pass's output against its input;
    Unity's D3D draws them unflipped, so they keep D3D's clip space."""
    lines = open(shader, encoding="utf-8", errors="replace").read().split("\n")
    lo, hi = passes(lines)[pass_index]
    bl = blocks(lines, lo, hi)
    vb, pb = pick(bl, "vertex", keywords), pick(bl, "pixel", keywords)
    vtext = balanced("\n".join(lines[vb["start"]:vb["end"]]))
    ptext = balanced("\n".join(lines[pb["start"]:pb["end"]]))
    v2f = re.search(r"struct\s+v2f\s*\{.*?\};", vtext, re.S).group(0)
    tmp = tempfile.mkdtemp(prefix="agwgsl_")
    try:
        mods = {}
        # one unit, as Unity compiles a variant: each block declares a uniform once under an
        # #ifndef guard, so the pixel block may use what only the vertex block declared
        same = (vb["start"], vb["end"]) == (pb["start"], pb["end"])
        unit = standalone(vtext if same else vtext + "\n" + ptext)
        for stage, entry, profile in (("vertex", "vert", "vs_6_0"), ("fragment", "frag", "ps_6_0")):
            src = PRELUDE + unit
            hl = os.path.join(tmp, f"{entry}.hlsl")
            open(hl, "w", encoding="utf-8").write(src)
            spv = os.path.join(tmp, f"{entry}.spv")
            run([dxc, "-HV", "2018", "-T", profile, "-E", entry, "-spirv", "-fspv-target-env=vulkan1.1",
                 "-fvk-use-dx-position-w", "-Fo", spv, hl])
            wg = os.path.join(tmp, f"{entry}.wgsl")
            run(["naga", *(["--keep-coordinate-space"] if texture_space else []), spv, wg])
            mods[stage] = open(wg, encoding="utf-8").read()
            if os.environ.get("AG_KEEP_HLSL"):
                shutil.copyfile(hl, out + f".{entry}.hlsl")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # $Globals holds only what each stage reads, so the two stages' blocks differ:
    # each keeps its own (u0024_Globals_vertex / _fragment) at its own binding
    for stage in ("vertex", "fragment"):
        mods[stage] = re.sub(r"\bu0024_Globals\b", f"u0024_Globals_{stage}", mods[stage])
    # one binding space across both stages, numbered by resource name
    names = []
    for stage in ("vertex", "fragment"):
        for b in bindings(mods[stage]):
            if b["name"] not in names:
                names.append(b["name"])
    number = {n: i for i, n in enumerate(names)}
    for stage in ("vertex", "fragment"):
        mods[stage] = re.sub(r"@group\(\d+\)\s*@binding\(\d+\)(\s*\n?\s*var(?:<\w+>)?\s+(\w+):)",
                             lambda m: f"@group(0) @binding({number[m.group(2)]}){m.group(1)}", mods[stage])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    for stage in ("vertex", "fragment"):
        # D3D samples with derivatives inside branches the game's code takes per pixel; WGSL
        # refuses that by default. Its diagnostic switch keeps the game's code as written.
        open(f"{out}.{stage[:4]}.wgsl", "w", encoding="utf-8").write(
            "diagnostic(off, derivative_uniformity);\n" + mods[stage])
    info = {"shader": os.path.basename(shader), "pass": pass_index, "keywords": sorted(keywords),
            "vertexVariant": sorted(vb["keywords"]), "pixelVariant": sorted(pb["keywords"]),
            "bindings": {}, "structs": {}}
    for stage in ("vertex", "fragment"):
        lay = wgsl_layout(mods[stage])
        for b in bindings(mods[stage]):
            entry = info["bindings"].setdefault(b["name"], {**b, "stages": []})
            entry["stages"].append(stage)
            st = re.sub(r"^.*?(\w+)$", r"\1", b["type"])
            if b["space"] == "uniform" and st in lay:
                info["structs"][b["name"]] = lay[st]
        sig = re.search(r"fn (?:vert|frag)\((.*?)\)\s*->", mods[stage], re.S)
        info[f"{stage}Inputs"] = [{"location": int(l), "name": n, "type": t} for l, n, t in
                                  re.findall(r"@location\((\d+)\)\s+(\w+):\s*([\w<>]+)", sig.group(1))]
    json.dump(info, open(out + ".json", "w", encoding="utf-8"), indent=1)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shader")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--pass", dest="pass_index", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dxc", default=os.environ.get("DXC", "dxc"))
    a = ap.parse_args()
    info = translate(a.shader, {k for k in a.keywords.split(",") if k}, a.pass_index, a.out, a.dxc)
    print(f"vertex {info['vertexVariant']}  pixel {info['pixelVariant']}  "
          f"{len(info['bindings'])} bindings -> {a.out}.vert.wgsl / .frag.wgsl / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

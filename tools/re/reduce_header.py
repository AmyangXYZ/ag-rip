"""Cut il2cpp.h (67 MB) down to the types the render pipeline needs, for Ghidra.

    python tools/re/reduce_header.py
      -> AG_cache/re/pipeline.h          structs (Ghidra-parsable: no ': Base', stdint typedefs)
      -> AG_cache/re/signatures.txt      "<rva hex>|<C signature>" for every pipeline method

Selected: every class with a method in targets_all.txt (the pipeline namespaces) plus
the value types they embed (Color, Vector*, Matrix4x4, Bounds, ...), transitively.
Pointers to anything else become void*.
"""
import json
import os
import re

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RE = os.path.join(TOP, "AG_cache", "re")
H = os.path.join(RE, "dump", "il2cpp.h")

PRELUDE = """typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef signed char int8_t;
typedef short int16_t;
typedef int int32_t;
typedef long long int64_t;
typedef long long intptr_t;
typedef unsigned long long uintptr_t;
typedef unsigned long long size_t;
typedef unsigned short Il2CppChar;
"""
PREFIXES = ("UnityEngine.Rendering.ReplicaExt", "UnityEngine.Rendering.Replica.", "UnityEngine.Pipelines",
            "ReplicaExt.", "SceneSetting$$", "InnerSceneSetting$$", "UnityEngine.Rendering.Volume",
            "EmssiveSwitch$$")


def blocks(text):
    """name -> full text of each top-level struct/union definition."""
    out = {}
    for m in re.finditer(r"^(struct|union) (?:__declspec\(align\(\d+\)\) )?(\w+)(?: : (\w+))?\s*\{\n(.*?)^\};\n",
                         text, re.M | re.S):
        kind, name, base, body = m.groups()
        out[name] = (kind, base, body)
    return out


def main():
    text = open(H, encoding="utf-8").read()
    B = blocks(text)
    d = json.load(open(os.path.join(RE, "dump", "script.json"), encoding="utf-8"))
    methods = [m for m in d["ScriptMethod"] if m["Name"].startswith(PREFIXES)]

    # classes named in the pipeline methods' signatures (this + params)
    want = set()
    for m in methods:
        for t in re.findall(r"(\w+)_o\*", m["Signature"]):
            want.add(t)
    selected = set()
    for t in want:
        for suf in ("_o", "_Fields", "_StaticFields", "_c"):
            if t + suf in B:
                selected.add(t + suf)

    order, seen = [], set()

    def need(name):
        """include a struct by value (and its by-value dependencies) first."""
        if name in seen or name not in B:
            return
        seen.add(name)
        kind, base, body = B[name]
        if base:
            need(base)
        for dep in re.findall(r"^\s*(?:struct |union )?(\w+) \w+(?:\[\d+\])?;", body, re.M):
            need(dep)
        order.append(name)

    for n in sorted(selected):
        need(n)
    keep = set(order)

    def fix_body(body):
        lines = []
        for ln in body.splitlines():
            m = re.match(r"^(\s*)(const )?(?:struct |union )?(\w+)\s*(\*+)\s*(\w+)(\[\d+\])?;", ln)
            if m and m.group(3) not in keep and m.group(3) not in ("MethodInfo", "Il2CppObject", "char",
                                                                    "void", "Il2CppChar"):
                ln = f"{m.group(1)}void{m.group(4)} {m.group(5)}{m.group(6) or ''};"
            lines.append(ln)
        return "\n".join(lines)

    # il2cpp's own runtime structs (MethodInfo, Il2CppClass_1, ...) precede the generated ones
    first = text.index("_Fields")
    first = text.rfind("\nstruct", 0, first)
    prelude = text[:first].replace("__declspec(align(8)) ", "")
    out = [PRELUDE, prelude]
    for n in order:     # forward declarations for pointers between kept structs
        out.append(f"{B[n][0]} {n};")
    for n in order:
        kind, base, body = B[n]
        head = f"{kind} {n} {{\n"
        if base:
            head += f"\tstruct {base} super;\n"
        out.append(head + fix_body(body) + "\n};")
    open(os.path.join(RE, "pipeline.h"), "w", encoding="utf-8").write("\n".join(out) + "\n")

    sig_out = []
    # also the engine API the pipeline calls (float args are in SSE registers: without a
    # signature Ghidra drops them, e.g. Material.SetFloatImpl(mat, id, value))
    api = [m for m in d["ScriptMethod"] if m["Name"].startswith(("UnityEngine.", "System.Math", "UnityEngine_"))
           and not m["Name"].startswith(PREFIXES)]
    for m in methods + api:
        s = m["Signature"]

        def repl(mm):
            t = mm.group(1)
            return mm.group(0) if t in keep or t in ("MethodInfo", "Il2CppObject") else "void*"
        s = re.sub(r"(?:struct )?(\w+)\*", repl, s)
        s = re.sub(r"\b(\w+_o) (\w+)", lambda mm: mm.group(0) if mm.group(1) in keep else f"void* {mm.group(2)}", s)
        sig_out.append(f"{m['Address']:x}|{s.rstrip(';')}")
    open(os.path.join(RE, "signatures.txt"), "w", encoding="utf-8").write("\n".join(sig_out) + "\n")
    print(f"{len(order)} structs, {len(sig_out)} signatures, header {sum(map(len, out)) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

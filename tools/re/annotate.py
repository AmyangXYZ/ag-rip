"""Make Ghidra output of volume-based pipeline code readable.

    python tools/re/annotate.py <decomp .c> [more ...]   -> prints annotated C

- a variable assigned from GetComponent<X> / VolumeDefaultsManager.Get<X> is typed X;
  its slots pVar[k].klass / .monitor (offset 16k / 16k+8) become X.<field name>
- the VolumeParameter virtual getter call  (**(code **)(vt + 0x218))(p, ...)  -> VALUE(p)
- il2cpp init noise (class init checks, metadata-usage inits) is dropped
Field layouts come from AG_cache/re/dump/dump.cs.
"""
import os
import re
import sys

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DUMP = os.path.join(TOP, "AG_cache", "re", "dump", "dump.cs")
_layout = {}


def layout(cls):
    if cls not in _layout:
        text = open(DUMP, encoding="utf-8").read() if "_text" not in _layout else _layout["_text"]
        _layout["_text"] = text
        m = re.search(rf"\bclass {re.escape(cls)}\b[^\n]*\n\{{(.*?)\n\t// Methods", text, re.S)
        fields = {}
        if m:
            for fm in re.finditer(r"(\w+) (\w+); // 0x([0-9A-F]+)", m.group(1)):
                fields[int(fm.group(3), 16)] = fm.group(2)
        _layout[cls] = fields
    return _layout[cls]


_statics = {}


def static_layout(typeinfo):
    """'UnityEngine_Rendering_ReplicaExt_FinalPass_ShaderIds_TypeInfo' -> {offset: name} of its
    static fields (the class name is found by trying dotted suffixes against dump.cs)."""
    if typeinfo in _statics:
        return _statics[typeinfo]
    text = _layout.get("_text") or open(DUMP, encoding="utf-8").read()
    _layout["_text"] = text
    parts = typeinfo[:-len("_TypeInfo")].lstrip("_").split("_")
    fields, label = {}, typeinfo
    for k in range(min(5, len(parts)), 0, -1):          # longest (most specific) name first
        cand = ".".join(parts[-k:])
        m = re.search(rf"\bclass {re.escape(cand)}\b[^\n]*\n\{{(.*?)\n\t// (?:Properties|Methods)", text, re.S)
        if m:
            for fm in re.finditer(r"static[^;\n]* (\S+); // 0x([0-9A-F]+)", m.group(1)):
                fields[int(fm.group(2), 16)] = fm.group(1)
            label = cand
            break
    _statics[typeinfo] = (label, fields)
    return _statics[typeinfo]


def statics(src):
    def repl(m):
        label, fields = static_layout(m.group(2))
        off = int(m.group(3), 16) if m.group(3) else 0
        return f"{label}.{fields.get(off, hex(off))}"
    # *(T *)(*(longlong *)(_X_TypeInfo + 0xb8) + 0xNN)   and   **(T **)(_X_TypeInfo + 0xb8)
    src = re.sub(r"\*\((\w+ \*)\)\s*\(\*\(longlong \*\)\s*\((_\w+_TypeInfo) \+ 0xb8\)\s*\+\s*(?:0x)?([0-9a-fA-F]+)\)",
                 lambda m: repl(type("M", (), {"group": lambda s, i: [None, m.group(1), m.group(2), m.group(3)][i]})()), src)
    src = re.sub(r"\*\*\((\w+ \*\*)\)\s*\((_\w+_TypeInfo) \+ 0xb8\)",
                 lambda m: repl(type("M", (), {"group": lambda s, i: [None, m.group(1), m.group(2), None][i]})()), src)
    return src


NOISE = re.compile(r"func_0x0001804fd580|FUN_18051ec00\(|il2cpp_runtime_class_init|_TypeInfo \+ 0xe0\) == 0|"
                   r"cctor_finished|cRam[0-9a-f]+ ==|cRam[0-9a-f]+ = '")


def annotate(src):
    types = {}
    for m in re.finditer(r"(\w+) = [^;]*?(?:GetComponent|VolumeDefaultsManager_Get|VolumeDefaultsManager__Get)"
                         r"[^;]*?_mi_Method_[\w.]*?(?:GetComponent|Get)<(\w+)>", src, re.S):
        types[m.group(1)] = m.group(2)

    def slot(m):
        var, k, part = m.group(1), int(m.group(2)), m.group(3)
        cls = types.get(var)
        off = 16 * k + (8 if part == "monitor" else 0)
        name = layout(cls).get(off) if cls else None
        return f"{cls}.{name}" if name else f"{var}@{off:#x}"

    src = re.sub(r"\b(\w+)\[(\d+)\]\.(klass|monitor)", slot, src)
    src = statics(src)
    # parameter.value getters
    src = re.sub(r"\(\*\*\(code \*\*\)\(\(longlong\)\w+ \+ 0x218\)\)\s*\(([^,()]+),[^;]*?0x220\)\)", r"VALUE(\1)", src)
    src = re.sub(r"\(\*\*\(code \*\*\)\(\*(\w+) \+ 0x218\)\)\s*\(\1,[^;]*?0x220\)\)", r"VALUE(\1)", src)
    src = re.sub(r"\(\*\*\(code \*\*\)\(\*(\w+) \+ 0x218\)\)\s*\((&\w+),\1,[^;]*?0x220\)\)", r"VALUE(\1)", src)
    out = []
    for ln in src.splitlines():
        if NOISE.search(ln) or not ln.strip():
            continue
        out.append(ln)
    text = "\n".join(out)
    # "pIVar2 = EnvironmentSetting.fogEnable; ... VALUE(pIVar2)" -> VALUE(EnvironmentSetting.fogEnable)
    for _ in range(3):
        for m in list(re.finditer(r"(\w+) = ([A-Za-z]+\.\w+);", text)):
            var, what = m.groups()
            nxt = text.find(f"{var} = ", m.end())
            seg_end = nxt if nxt >= 0 else len(text)
            text = text[:m.end()] + text[m.end():seg_end].replace(f"VALUE({var})", f"VALUE({what})") + text[seg_end:]
    return text


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(annotate(open(f, encoding="utf-8").read()))

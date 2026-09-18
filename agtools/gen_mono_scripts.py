#!/usr/bin/env python3
r"""
Give AssetRipper's empty script stubs their real serialized fields.

The game is IL2CPP with encrypted metadata, so AssetRipper writes every game
component (SceneSetting, SimMainLight, ReplicaAdditionalLightData, ...) as an
empty class. The scene file still carries each component's data, but nothing can
read it: the Inspector shows nothing, a script can't use it, and Unity drops the
unknown fields the next time the scene is saved.

The bundles do carry each MonoBehaviour's *type tree* - the exact serialized
layout. This rebuilds every stub from it: fields in order, nested structs as
nested [Serializable] classes, lists, colours/vectors/curves, object references.
Field names Unity serialized in a form C# can't spell (auto-property backing
fields) get a legal name plus [FormerlySerializedAs] with the original.

    python agtools/gen_mono_scripts.py <project dir> <bundle> [<bundle> ...]

References to other game scripts are typed MonoBehaviour / ScriptableObject /
Object so the per-assembly asmdefs need no cross references; the serialized
reference is the same.
"""
from __future__ import annotations

import os
import re
import sys
import warnings

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
warnings.simplefilter("ignore")

BASE_FIELDS = {"m_GameObject", "m_Enabled", "m_Script", "m_Name", "m_EditorHideFlags",
               "m_EditorClassIdentifier", "m_ObjectHideFlags"}
PRIMS = {"bool": "bool", "UInt8": "byte", "SInt8": "sbyte", "char": "char",
         "SInt16": "short", "short": "short", "UInt16": "ushort", "unsigned short": "ushort",
         "int": "int", "SInt32": "int", "UInt32": "uint", "unsigned int": "uint",
         "SInt64": "long", "long long": "long", "UInt64": "ulong", "unsigned long long": "ulong",
         "float": "float", "double": "double", "string": "string"}
UNITY = {"ColorRGBA": "Color", "Vector2f": "Vector2", "Vector3f": "Vector3", "Vector4f": "Vector4",
         "Quaternionf": "Quaternion", "Matrix4x4f": "Matrix4x4", "Rectf": "Rect",
         "AnimationCurve": "AnimationCurve", "Gradient": "Gradient", "BitField": "LayerMask",
         "AABB": "Bounds", "int2_storage": "Vector2Int", "int3_storage": "Vector3Int",
         "GUIStyle": "GUIStyle", "RectOffset": "RectOffset", "Hash128": "Hash128"}
PPTR = {"GameObject": "GameObject", "Transform": "Transform", "RectTransform": "RectTransform",
        "Texture": "Texture", "Texture2D": "Texture2D", "Texture3D": "Texture3D",
        "Cubemap": "Cubemap", "RenderTexture": "RenderTexture", "Material": "Material",
        "Mesh": "Mesh", "Shader": "Shader", "Light": "Light", "Camera": "Camera",
        "Renderer": "Renderer", "MeshRenderer": "MeshRenderer", "MeshFilter": "MeshFilter",
        "SkinnedMeshRenderer": "SkinnedMeshRenderer", "Animator": "Animator",
        "AnimationClip": "AnimationClip", "AudioClip": "AudioClip", "Sprite": "Sprite",
        "ParticleSystem": "ParticleSystem", "Collider": "Collider", "Component": "Component",
        "MonoBehaviour": "MonoBehaviour", "ScriptableObject": "ScriptableObject",
        "ReflectionProbe": "ReflectionProbe", "Font": "Font", "TextAsset": "TextAsset",
        "Object": "Object", "EditorExtension": "Object", "NamedObject": "Object"}


def ident(name: str) -> str:
    s = re.sub(r"\W", "_", name)
    return s if s and not s[0].isdigit() else "_" + s


class Gen:
    def __init__(self):
        self.nested: dict[str, list[str]] = {}   # nested type name -> body lines
        self.order: list[str] = []

    def type_of(self, node) -> str:
        t = node.m_Type
        if t in PRIMS:
            return PRIMS[t]
        if t in UNITY:
            return UNITY[t]
        if t.startswith("PPtr<"):
            # managed type trees write every reference as PPtr<$Type>, engine types
            # included (PPtr<$Material>); unknown ones are game classes, which may be
            # components or ScriptableObjects - Object holds either
            inner = t[5:-1].lstrip("$")
            return PPTR.get(inner, "Object")
        if t in ("vector", "staticvector", "set") or (node.m_Children and node.m_Children[0].m_Type == "Array"):
            arr = node.m_Children[0]
            data = arr.m_Children[1] if len(arr.m_Children) > 1 else None
            return f"List<{self.type_of(data) if data else 'byte'}>"
        if t == "Array":
            data = node.m_Children[1]
            return f"List<{self.type_of(data)}>"
        if t in ("ReferencedObject", "managedRefArrayItem"):
            return "object"
        # a serializable struct/class of the game's
        name = ident(t)
        if name not in self.nested:
            self.nested[name] = []
            self.order.append(name)
            self.nested[name] = self.fields(node.m_Children)
        return name

    def fields(self, children) -> list[str]:
        out, used = [], set()
        for c in children:
            if c.m_Name in BASE_FIELDS:
                continue
            if c.m_Type in ("managedReferencesRegistry", "ReferencesRegistry"):
                continue
            ctype = self.type_of(c)
            fname = ident(c.m_Name)
            while fname in used:
                fname += "_"
            used.add(fname)
            attrs = ["SerializeField"]
            if fname != c.m_Name:
                attrs.append(f'FormerlySerializedAs("{c.m_Name}")')
            out.append(f"    [{', '.join(attrs)}] public {ctype} {fname};")
        return out


def collect(bundles: list[str]) -> dict:
    """(namespace, class) -> (assembly, root type tree node, is_asset)."""
    found = {}
    for b in bundles:
        try:
            env = UnityPy.load(b)
        except Exception:                                             # noqa: BLE001
            continue
        for o in env.objects:
            if o.type.name != "MonoBehaviour" or not o.serialized_type or not o.serialized_type.node:
                continue
            try:
                d = o.read(check_read=False)
                scr = d.m_Script.read()
            except Exception:                                         # noqa: BLE001
                continue
            key = (scr.m_Namespace, scr.m_ClassName)
            if key in found:
                continue
            is_asset = getattr(d.m_GameObject, "path_id", 0) == 0
            found[key] = (scr.m_AssemblyName, o.serialized_type.node, is_asset)
    return found


def stub_files(project: str) -> dict:
    """(namespace, class) -> path, for AssetRipper stubs only."""
    out = {}
    scripts = os.path.join(project, "ExportedProject", "Assets", "Scripts")
    for dp, _, fns in os.walk(scripts):
        for fn in fns:
            if not fn.endswith(".cs"):
                continue
            p = os.path.join(dp, fn)
            text = open(p, encoding="utf-8-sig", errors="replace").read()
            if "Dummy class" not in text:
                continue
            m = re.search(r"class\s+(\w+)\s*:\s*([\w.]+)", text)
            ns = re.search(r"namespace\s+([\w.]+)", text)
            if m:
                out[(ns.group(1) if ns else "", m.group(1))] = (p, m.group(2))
    return out


def render(ns: str, cls: str, base: str, node) -> str:
    g = Gen()
    body = g.fields(node.m_Children)
    lines = ["// Serialized layout rebuilt from the game's type tree (agtools/gen_mono_scripts.py).",
             "// Fields only - the game's code is not recoverable.",
             "using System;", "using System.Collections.Generic;", "using UnityEngine;",
             "using UnityEngine.Serialization;", "using Object = UnityEngine.Object;", ""]
    ind = ""
    if ns:
        lines.append(f"namespace {ns}")
        lines.append("{")
        ind = "    "
    lines.append(f"{ind}public class {cls} : {base}")
    lines.append(f"{ind}{{")
    for name in g.order:
        lines.append(f"{ind}    [Serializable]")
        lines.append(f"{ind}    public class {name}")
        lines.append(f"{ind}    {{")
        lines += [f"{ind}    {l}" for l in g.nested[name]]
        lines.append(f"{ind}    }}")
        lines.append("")
    lines += [f"{ind}{l}" for l in body]
    lines.append(f"{ind}}}")
    if ns:
        lines.append("}")
    return "\n".join(lines) + "\n"


def generate(project: str, bundles: list[str]) -> tuple[int, list[str]]:
    stubs = stub_files(project)
    found = collect(bundles)
    written, missing = 0, []
    for key, (asm, node, is_asset) in sorted(found.items()):
        if key not in stubs:
            missing.append(".".join(k for k in key if k))
            continue
        path, base = stubs[key]
        if is_asset and base == "MonoBehaviour":
            # standalone assets (VolumeProfile and its components) must be
            # ScriptableObjects or Unity refuses to load their data
            base = "ScriptableObject"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(key[0], key[1], base, node))
        written += 1
    return written, missing


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    written, missing = generate(sys.argv[1], sys.argv[2:])
    print(f"{written} script(s) rebuilt" + (f"; no stub for: {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

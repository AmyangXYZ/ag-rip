"""Write tools/uscs/builtin_names.txt: every uniform/texture Unity's built-in
CGPROGRAM includes already declare, so USCSandbox skips re-declaring them.

    python tools/uscs_builtin_names.py [<Unity Editor/Data/Resources/CGIncludes>]
"""
import os
import re
import sys

D = sys.argv[1] if len(sys.argv) > 1 else \
    r"C:\Program Files\Unity\Hub\Editor\6000.6.1f1\Editor\Data\Resources\CGIncludes"
FILES = ["UnityShaderVariables.cginc", "HLSLSupport.cginc", "UnityShaderUtilities.cginc",
         "UnityInstancing.cginc"]
names = set()
for f in FILES:
    s = open(os.path.join(D, f), encoding="utf-8", errors="replace").read()
    s = re.sub(r"//.*", "", s)
    for m in re.finditer(r"^\s*(?:uniform\s+)?(?:static\s+)?(?:float|half|fixed|int|uint|bool)"
                         r"(?:[1-4](?:x[1-4])?)?\s+(\w+)\s*(?:\[[^\]]*\])?\s*;", s, re.M):
        names.add(m.group(1))
    for m in re.finditer(r"UNITY_DECLARE_TEX\w*\((\w+)", s):
        names.add(m.group(1))
    for m in re.finditer(r"^\s*(?:sampler\w*|Texture\w*(?:<[^>]*>)?)\s+(\w+)\s*;", s, re.M):
        names.add(m.group(1))
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uscs", "builtin_names.txt")
with open(out, "w") as fh:
    fh.write("\n".join(sorted(names)))
print(len(names), "names ->", out)
print([n for n in sorted(names) if re.search("LOD|Transform|RenderingLayer|SpecCube0", n)])

"""Dump the constant tables of a LuaJIT 2.x bytecode file (stripped or not) - enough to
read Aether Gazer's config modules (assets/luabuilds/.../game/config/*.lua.bytes), whose
bodies are table templates (TDUP) with nested constants.

    python tools/re/ljdump.py <file.lua.bytes> [--json]
"""
import json
import struct
import sys


class R:
    def __init__(self, b):
        self.b, self.p = b, 0

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def uleb(self):
        v, s = 0, 0
        while True:
            c = self.u8()
            v |= (c & 0x7F) << s
            s += 7
            if c < 0x80:
                return v

    def bytes(self, n):
        v = self.b[self.p:self.p + n]
        self.p += n
        return v


def uleb33(r):          # first uleb of a number constant carries the is-float flag in bit 0
    c = r.u8()
    v, s = c >> 1, 6
    while c >= 0x80:
        c = r.u8()
        v |= (c & 0x7F) << s
        s += 7
    return v


def ktabk(r):
    t = r.uleb()
    if t == 0:
        return None
    if t == 1:
        return False
    if t == 2:
        return True
    if t == 3:
        v = r.uleb()
        return v - (1 << 32) if v >= 1 << 31 else v
    if t == 4:
        lo, hi = r.uleb(), r.uleb()
        return struct.unpack("<d", struct.pack("<II", lo, hi))[0]
    return r.bytes(t - 5).decode("utf-8", "replace")


def table(r):
    narr, nhash = r.uleb(), r.uleb()
    arr = [ktabk(r) for _ in range(narr)]
    h = {}
    for _ in range(nhash):
        k = ktabk(r)
        h[k] = ktabk(r)
    if arr and arr[0] is None:
        arr = arr[1:]
    if h and not arr:
        return h
    if not h:
        return arr
    return {"__array": arr, **{str(k): v for k, v in h.items()}}


def protos(b):
    r = R(b)
    assert r.bytes(3) == b"\x1bLJ", "not LuaJIT bytecode"
    r.u8()                      # version
    flags = r.uleb()
    if not flags & 2:           # not stripped: chunk name
        r.bytes(r.uleb())
    out = []
    while True:
        n = r.uleb()
        if n == 0:
            break
        end = r.p + n
        pflags, nparams, fsize, nuv = r.u8(), r.u8(), r.u8(), r.u8()
        nkgc, nkn, nbc = r.uleb(), r.uleb(), r.uleb()
        dbg = 0
        if not flags & 2:
            dbg = r.uleb()
            if dbg:
                r.uleb()
                r.uleb()
        r.bytes(nbc * 4 + nuv * 2)
        kgc = []
        for _ in range(nkgc):
            t = r.uleb()
            if t == 0:
                kgc.append("<proto>")
            elif t == 1:
                kgc.append(table(r))
            elif t in (2, 3):
                lo, hi = r.uleb(), r.uleb()
                kgc.append((hi << 32) | lo)
            elif t == 4:
                kgc.append(("complex", r.uleb(), r.uleb(), r.uleb(), r.uleb()))
            else:
                kgc.append(r.bytes(t - 5).decode("utf-8", "replace"))
        kn = []
        for _ in range(nkn):
            isnum = r.b[r.p] & 1
            v = uleb33(r)
            if isnum:
                hi = r.uleb()
                kn.append(struct.unpack("<d", struct.pack("<II", v, hi))[0])
            else:
                kn.append(v - (1 << 32) if v >= 1 << 31 else v)
        r.p = end
        out.append({"kgc": kgc, "kn": kn})
    return out


if __name__ == "__main__":
    ps = protos(open(sys.argv[1], "rb").read())
    if "--json" in sys.argv:
        print(json.dumps(ps, ensure_ascii=False, indent=1, default=str))
    else:
        for i, p in enumerate(ps):
            print(f"-- proto {i}: {len(p['kgc'])} gc consts, {len(p['kn'])} numbers")
            for k in p["kgc"]:
                print("  ", json.dumps(k, ensure_ascii=False, default=str)[:300])

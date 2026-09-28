"""Decrypt a HybridCLR "CDPH" hot-update assembly (splash/huds, splash/amds) into a PE
that ILSpy / dnfile read, with the game's own block routine (codephil.Emu).

CDPH layout (GameAssembly rva 0x4d73b0 parses it):
    'CDPH' u32 ver=1 | u32 flag | u32 0 | key[256] @0x10
    8 x (u32 len, bytes) from 0x110, 4-aligned: per-stream "programs" P0..P7
    16-byte check block: decrypts (program = ~i, 256 B) to "Hello, HybridCLR"
    u32 0, u32 metadata_root, u32 ?, u32 nsec, 8 B, nsec x (va, size, 4, raw)
    image: RVA == file offset; a plain ECMA-335 metadata root ('BSJB') at metadata_root
Encryption (the CDPH image vtable, rva 0x4652af0):
    #Strings P1, #Blob P2, #US P3, #~ P5, in 256-byte blocks, at load   (slot 2)
    each #US string again P4, 16-byte blocks, on fetch                     (slot 4)
    one table's rows P6, a row per block, on access                        (slot 13)
    method bodies P7, 16-byte blocks, before first run                     (slot 5)

    python tools/re/cdph.py <in.bytes> <out.dll>
"""
import struct
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
import codephil as cp  # noqa: E402
from unicorn.x86_const import (UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9,  # noqa: E402
                               UC_X86_REG_RSP)


class Emu(cp.Emu):
    def block(self, program: bytes, key: bytes, data: bytes) -> bytes:
        uc, s = self.uc, self.SCRATCH
        uc.mem_write(s, program.ljust(0x100, b"\0"))
        uc.mem_write(s + 0x100, key.ljust(0x100, b"\0"))
        uc.mem_write(s + 0x200, data.ljust(0x100, b"\0"))
        sp = self.STACK + 0x80000
        uc.mem_write(sp, struct.pack("<Q", self.RET))
        uc.mem_write(sp + 0x28, struct.pack("<I", len(data)))
        uc.reg_write(UC_X86_REG_RSP, sp)
        uc.reg_write(UC_X86_REG_RCX, s)
        uc.reg_write(UC_X86_REG_RDX, len(program))
        uc.reg_write(UC_X86_REG_R8, s + 0x100)
        uc.reg_write(UC_X86_REG_R9, s + 0x200)
        uc.emu_start(self.base + cp.BLOCK_FN, self.RET)
        return bytes(uc.mem_read(s + 0x200, len(data)))

    def chunks(self, program, key, data, bs):
        return b"".join(self.block(program, key, data[i:i + bs]) for i in range(0, len(data), bs))


def parse(b: bytes):
    assert b[:4] == b"CDPH", b[:4]
    key = bytes(b[0x10:0x110])
    off, progs = 0x110, []
    for _ in range(8):
        n, = struct.unpack_from("<I", b, off)
        progs.append(bytes(b[off + 4:off + 4 + n]))
        off = (off + 4 + n + 3) & ~3
    check = bytes(b[off:off + 16])
    _, root, _, nsec = struct.unpack_from("<4I", b, off + 16)
    return key, progs, check, root


def streams(b, root):
    _, _, _, _, vlen = struct.unpack_from("<IHHII", b, root)
    p = root + 16 + vlen
    _, ns = struct.unpack_from("<HH", b, p)
    p += 4
    out = {}
    for _ in range(ns):
        o, sz = struct.unpack_from("<II", b, p)
        p += 8
        e = b.index(b"\0", p)
        out[b[p:e].decode()] = (root + o, sz)
        p = (e + 4) & ~3
    return out


def decrypt_heaps(b: bytearray, emu, key, progs, S):
    for name, pg in (("#Strings", 1), ("#Blob", 2), ("#US", 3), ("#~", 5)):
        o, sz = S[name]
        b[o:o + sz] = emu.chunks(progs[pg], key, bytes(b[o:o + sz]), 0x100)
    # user strings: a second layer per entry (compressed length, then its bytes)
    o, sz = S["#US"]
    p, end = o + 1, o + sz
    while p < end:
        c = b[p]
        if c < 0x80:
            n, h = c, 1
        elif c < 0xC0:
            n, h = ((c & 0x3F) << 8) | b[p + 1], 2
        else:
            n, h = ((c & 0x1F) << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3], 4
        if n:
            b[p + h:p + h + n] = emu.chunks(progs[4], key, bytes(b[p + h:p + h + n]), 0x10)
        p += h + n


def wrap_pe(b: bytearray, root: int, msize: int) -> bytes:
    """A PE32 (I386, IL-only) whose single section maps the file 1:1 from 0x200, so
    every RVA in the metadata is its file offset; the CLI header sits at 0x200."""
    SA = FA = 0x200
    img = bytearray(b)
    size = len(img)
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 0xE0, 0x2102)
    dd = [(0, 0)] * 16
    dd[14] = (0x200, 0x48)
    opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", 0x10B, 8, 0, size - 0x200, 0, 0, 0, 0x200, 0, 0x400000,
                      SA, FA, 4, 0, 0, 0, 4, 0, 0, (size + SA - 1) // SA * SA, 0x200, 0, 3, 0x8540,
                      0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", *d) for d in dd)
    sec = struct.pack("<8sIIIIIIHHI", b".text", size - 0x200, 0x200, size - 0x200, 0x200, 0, 0, 0, 0, 0x60000020)
    hdr = bytes(dos) + b"PE\0\0" + coff + opt + sec
    assert len(hdr) <= 0x200
    img[0:0x200] = hdr.ljust(0x200, b"\0")
    cli = struct.pack("<IHHIIIIIIIIIIIIIIII", 0x48, 2, 5, root, msize, 1, 0, *([0] * 12))
    img[0x200:0x200 + 0x48] = cli
    return bytes(img)


def main():
    b = bytearray(open(sys.argv[1], "rb").read())
    key, progs, check, root = parse(b)
    emu = Emu()
    assert emu.block(bytes((~i) & 0xFF for i in range(256)), key, check) == b"Hello, HybridCLR"
    S = streams(b, root)
    decrypt_heaps(b, emu, key, progs, S)
    msize = max(o + sz for o, sz in S.values()) - root
    img = bytearray(wrap_pe(b, root, msize))
    # the tables need the heaps first: parse this stage with dnfile for the TypeDef
    # table's place and every method body, then take off the last two layers
    import dnfile
    import tempfile
    import os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dll")
    tmp.write(img)
    tmp.close()
    dpe = dnfile.dnPE(tmp.name)
    md = dpe.net.mdtables
    t = md.TypeDef
    for i in range(t.num_rows):
        o = t.file_offset + i * t.row_size
        img[o:o + t.row_size] = emu.block(progs[6], key, bytes(img[o:o + t.row_size]))
    n, seen = 0, set()
    for r in md.MethodDef.rows:
        rva = r.Rva
        if not rva or rva in seen:           # identical bodies share one RVA: decrypt it once
            continue
        seen.add(rva)
        h = img[rva]
        if h & 3 == 2:                       # tiny header: code size in the top 6 bits
            hs, cs = 1, h >> 2
        else:                                # fat header: flags/size, maxstack, code size
            fl, _, cs, _ = struct.unpack_from("<HHII", img, rva)
            hs = (fl >> 12) * 4
        c = rva + hs
        img[c:c + cs] = emu.chunks(progs[7], key, bytes(img[c:c + cs]), 0x10)
        n += 1
    dpe.close()
    os.unlink(tmp.name)
    open(sys.argv[2], "wb").write(img)
    print("wrote", sys.argv[2], {k: v[1] for k, v in S.items()}, f"{t.num_rows} types, {n} method bodies")


if __name__ == "__main__":
    main()

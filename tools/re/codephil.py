"""Decrypt Aether Gazer's "CODEPHIL" containers by running the game's own block
routine (GameAssembly.dll, rva 0x6e11f0) under Unicorn - exact by construction.

Container (global-metadata.dat):
    u32 magic 0x1357FEDA | u32 payload length | 256-byte key | 64-byte program |
    payload (decrypted in 64-byte blocks: block_fn(program, 64, key, block, len))
    decrypted payload = b"CODEPHIL" + plaintext

HybridCLR DLLs (splash/amds, splash/huds TextAssets) carry a b"CDPH" header - see
decrypt_cdph().

    python tools/re/codephil.py metadata  -> AG_cache/re/global-metadata.dat
"""
import os
import struct
import sys

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_PROT_ALL
from unicorn.x86_const import (UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9,
                               UC_X86_REG_RSP)

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DLL = os.path.join(TOP, "AetherGazer", "GameAssembly.dll")
META = os.path.join(TOP, "AetherGazer", "AetherGazer_Data", "il2cpp_data", "Metadata", "global-metadata.dat")
OUT = os.path.join(TOP, "AG_cache", "re")
BLOCK_FN = 0x6e11f0
MAGIC = 0x1357FEDA


class Emu:
    STACK, SCRATCH, RET = 0x10000000, 0x20000000, 0x30000000

    def __init__(self):
        pe = pefile.PE(DLL, fast_load=True)
        self.base = pe.OPTIONAL_HEADER.ImageBase
        size = (pe.OPTIONAL_HEADER.SizeOfImage + 0xFFF) & ~0xFFF
        uc = Uc(UC_ARCH_X86, UC_MODE_64)
        uc.mem_map(self.base, size, UC_PROT_ALL)
        uc.mem_write(self.base, pe.header)
        for s in pe.sections:
            uc.mem_write(self.base + s.VirtualAddress, s.get_data()[:max(s.Misc_VirtualSize, s.SizeOfRawData)])
        uc.mem_map(self.STACK, 0x100000, UC_PROT_ALL)
        uc.mem_map(self.SCRATCH, 0x1000, UC_PROT_ALL)
        uc.mem_map(self.RET, 0x1000, UC_PROT_ALL)
        uc.mem_write(self.RET, b"\xc3")
        self.uc = uc

    def run(self, program: bytes, key: bytes, block: bytes) -> bytes:
        uc, s = self.uc, self.SCRATCH
        uc.mem_write(s, program)            # 64
        uc.mem_write(s + 0x40, key)         # 256
        uc.mem_write(s + 0x140, block.ljust(64, b"\0"))
        sp = self.STACK + 0x80000
        uc.mem_write(sp, struct.pack("<Q", self.RET))           # return address
        uc.mem_write(sp + 0x28, struct.pack("<I", len(block)))  # 5th arg (block length)
        uc.reg_write(UC_X86_REG_RSP, sp)
        uc.reg_write(UC_X86_REG_RCX, s)
        uc.reg_write(UC_X86_REG_RDX, len(program))
        uc.reg_write(UC_X86_REG_R8, s + 0x40)
        uc.reg_write(UC_X86_REG_R9, s + 0x140)
        uc.emu_start(self.base + BLOCK_FN, self.RET)
        return bytes(uc.mem_read(s + 0x140, len(block)))


def decrypt_container(buf: bytes, emu: Emu, off: int = 0) -> bytes:
    """buf[off:] = magic, len, key[256], program[64], payload[len]."""
    magic, n = struct.unpack_from("<II", buf, off)
    assert magic == MAGIC, hex(magic)
    key = buf[off + 8:off + 0x108]
    prog = buf[off + 0x108:off + 0x148]
    payload = buf[off + 0x148:off + 0x148 + n]
    out = bytearray()
    for i in range(0, n, 64):
        out += emu.run(prog, key, payload[i:i + 64])
        if i % (64 * 50000) == 0 and i:
            print(f"  {i / n:.0%}", flush=True)
    assert out[:8] == b"CODEPHIL", out[:16]
    return bytes(out[8:])


def main():
    os.makedirs(OUT, exist_ok=True)
    emu = Emu()
    if sys.argv[1] == "metadata":
        plain = decrypt_container(open(META, "rb").read(), emu)
        print("header", plain[:8].hex(), "version", struct.unpack_from("<I", plain, 4)[0])
        open(os.path.join(OUT, "global-metadata.dat"), "wb").write(plain)
    elif sys.argv[1] == "test":
        buf = open(META, "rb").read()
        magic, n = struct.unpack_from("<II", buf)
        blk = emu.run(buf[0x108:0x148], buf[8:0x108], buf[0x148:0x148 + 64])
        print(blk[:32].hex(), blk[:16])


if __name__ == "__main__":
    main()

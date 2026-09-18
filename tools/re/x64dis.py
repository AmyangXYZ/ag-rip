"""Tiny x64 disassembly helper for GameAssembly.dll (capstone + pefile).

    python tools/re/x64dis.py <file-offset|va> [count]      disassemble around an address
    python tools/re/x64dis.py --func <file-offset>         find the enclosing function (via .pdata) and dump it
"""
import sys
import capstone
import pefile

DLL = r"C:\AetherGazerStarter\AetherGazer\GameAssembly.dll"
pe = pefile.PE(DLL, fast_load=True)
pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXCEPTION"]])
data = open(DLL, "rb").read()
base = pe.OPTIONAL_HEADER.ImageBase
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = False


def off2rva(off):
    for s in pe.sections:
        if s.PointerToRawData <= off < s.PointerToRawData + s.SizeOfRawData:
            return off - s.PointerToRawData + s.VirtualAddress
    return None


def rva2off(rva):
    return pe.get_offset_from_rva(rva)


def func_of(rva):
    for e in pe.DIRECTORY_ENTRY_EXCEPTION:
        if e.struct.BeginAddress <= rva < e.struct.EndAddress:
            return e.struct.BeginAddress, e.struct.EndAddress
    return None


def dump(rva_start, rva_end, mark=None):
    off = rva2off(rva_start)
    code = data[off:off + (rva_end - rva_start)]
    for ins in md.disasm(code, base + rva_start):
        m = " <==" if mark is not None and ins.address - base <= mark < ins.address - base + ins.size else ""
        print(f"{ins.address:#x}: {ins.mnemonic:8s} {ins.op_str}{m}")


if __name__ == "__main__":
    if sys.argv[1] == "--func":
        off = int(sys.argv[2], 0)
        rva = off2rva(off)
        f = func_of(rva)
        print(f"file off {off:#x} -> rva {rva:#x}; function {f[0]:#x}..{f[1]:#x} ({f[1]-f[0]} bytes)")
        dump(*f, mark=rva)
    else:
        rva = int(sys.argv[1], 0)
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        dump(rva, rva + n)

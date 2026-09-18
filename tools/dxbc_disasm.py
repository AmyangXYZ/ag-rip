"""Disassemble a Unity DX11 program dump (AG_DUMP_DXBC) with the system d3dcompiler.

    python tools/dxbc_disasm.py <file.bin>

Unity prefixes the DXBC container with its own header; the container starts at
the 'DXBC' magic.
"""
import ctypes
import sys

data = open(sys.argv[1], "rb").read()
i = data.find(b"DXBC")
if i < 0:
    sys.exit("no DXBC container")
blob = data[i:]
size = int.from_bytes(blob[24:28], "little")
blob = blob[:size]

d3d = ctypes.WinDLL("d3dcompiler_47.dll")


class ID3DBlob(ctypes.Structure):
    pass


out = ctypes.c_void_p()
hr = d3d.D3DDisassemble(ctypes.c_char_p(blob), ctypes.c_size_t(len(blob)), 0, None, ctypes.byref(out))
if hr != 0:
    sys.exit(f"D3DDisassemble failed: {hr:#x}")
vtbl = ctypes.cast(out, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
get_ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vtbl[3])
get_size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vtbl[4])
p, n = get_ptr(out), get_size(out)
print(ctypes.string_at(p, n).decode("ascii", "replace"))

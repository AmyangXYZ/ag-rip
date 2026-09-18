#!/usr/bin/env python3
"""CRI @UTF tables (ACB cue sheets and the tables nested in them).

    python agtools/cri_utf.py <file.acb> [Table ...]     dump the header / named sub-tables

A @UTF table is big-endian: header, column schema, rows, a string pool and a data pool.
Each column has a storage flag (none / constant / per-row) and a type; 'data' cells are
byte blobs that are often @UTF tables themselves (ACB: CueTable, WaveformTable,
LipsMorphTable, ...). ACB headers are unencrypted @UTF.
"""
from __future__ import annotations

import struct
import sys

_TYPES = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f", 9: ">d"}


class Table:
    def __init__(self, name: str, columns: list[str], rows: list[dict]) -> None:
        self.name, self.columns, self.rows = name, columns, rows

    def __repr__(self) -> str:
        return f"<@UTF {self.name}: {len(self.rows)} rows, columns {self.columns}>"


def parse(buf: bytes, offset: int = 0) -> Table:
    b = buf[offset:]
    if b[:4] != b"@UTF":
        raise ValueError("not a @UTF table")
    size = struct.unpack_from(">I", b, 4)[0]
    t = b[8:8 + size]
    rows_off, str_off, data_off = struct.unpack_from(">HII", t, 2)   # skip version(2)
    name_off, ncols, row_size, nrows = struct.unpack_from(">IHHI", t, 12)
    strings, data = t[str_off:data_off], t[data_off:]

    def cstr(o: int) -> str:
        e = strings.index(b"\0", o)
        return strings[o:e].decode("utf-8", "replace")

    def value(kind: int, src: bytes, pos: int):
        if kind in _TYPES:
            fmt = _TYPES[kind]
            return struct.unpack_from(fmt, src, pos)[0], pos + struct.calcsize(fmt)
        if kind == 0xA:                                  # string
            return cstr(struct.unpack_from(">I", src, pos)[0]), pos + 4
        if kind == 0xB:                                  # data blob
            o, n = struct.unpack_from(">II", src, pos)
            return data[o:o + n], pos + 8
        raise ValueError(f"unknown @UTF type {kind:#x}")

    cols, pos = [], 24
    for _ in range(ncols):
        flags = t[pos]
        pos += 1
        name = cstr(struct.unpack_from(">I", t, pos)[0])
        pos += 4
        storage, kind = flags & 0xF0, flags & 0x0F
        const = None
        if storage == 0x30:                              # constant for every row
            const, pos = value(kind, t, pos)
        cols.append((name, storage, kind, const))
    rows = []
    for r in range(nrows):
        p = rows_off + r * row_size
        row = {}
        for name, storage, kind, const in cols:
            if storage == 0x50:                          # per-row value
                row[name], p = value(kind, t, p)
            elif storage == 0x30:
                row[name] = const
            else:
                row[name] = None
        rows.append(row)
    return Table(cstr(name_off), [c[0] for c in cols], rows)


def sub(table: Table, column: str, row: int = 0) -> Table | None:
    blob = table.rows[row].get(column)
    return parse(blob) if isinstance(blob, (bytes, bytearray)) and blob[:4] == b"@UTF" else None


def main() -> int:
    acb = parse(open(sys.argv[1], "rb").read())
    print(acb)
    for name in sys.argv[2:]:
        t = sub(acb, name)
        print(t)
        for r in (t.rows if t else [])[:10]:
            print("  ", {k: (f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else v) for k, v in r.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())

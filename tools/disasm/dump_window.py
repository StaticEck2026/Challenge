#!/usr/bin/env python3
"""Render NFL Challenge text-window data files (B-J, N-Q, M) as plain text.

usage: python3 -I tools/disasm/dump_window.py path/to/nfl/B [more files...] [--attr]

Format (see docs/FILE_FORMATS.md): byte rows, byte cols, then rows*cols
(char, attribute) pairs in row-major order, padded with zeros to a multiple
of 128 bytes.  M has a 12-byte prefix (six row,col pairs) before its
rows/cols header.  NFL.EXE loads these through 130A:0AB0.
"""

import argparse
import os


def load(path):
    with open(path, "rb") as f:
        d = f.read()
    pre = b""
    if os.path.basename(path).upper() == "M":
        pre, d = d[:12], d[12:]
    rows, cols = d[0], d[1]
    cells = d[2:2 + 2 * rows * cols]
    return pre, rows, cols, cells, len(d) + len(pre)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--attr", action="store_true", help="also print the attribute bytes in hex")
    a = ap.parse_args()
    for path in a.files:
        pre, rows, cols, cells, size = load(path)
        print(f"== {os.path.basename(path)}: {rows} rows x {cols} cols, {size} bytes"
              + (f", prefix {list(pre)}" if pre else ""))
        for y in range(rows):
            row = cells[2 * cols * y:2 * cols * (y + 1)]
            print("|" + bytes(row[0::2]).decode("cp437") + "|")
            if a.attr:
                print(" " + "".join(f"{c:02X}" for c in row[1::2]))


if __name__ == "__main__":
    main()

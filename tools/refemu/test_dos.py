#!/usr/bin/env python3
"""DOS service tests: assemble a .COM with nasm, run it in the full machine
(BIOS + DOS) against a temporary drive, and check what it wrote.

Run: python3 -I tools/refemu/test_dos.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from run import Session  # noqa: E402

SRC = r"""
bits 16
org 0x100
start:
    ; --- AH=29h parse "data.bin" into fcb, AH=0Fh open, AH=27h read 2 records of 4 bytes
    mov si, fname
    mov di, fcb
    mov ax, 0x2901
    int 0x21
    mov [r_parse], al
    mov dx, fcb
    mov ah, 0x0F
    int 0x21
    mov [r_open], al
    mov word [fcb+0x0E], 4          ; record size
    mov word [fcb+0x21], 1          ; random record 1
    mov word [fcb+0x23], 0
    mov dx, dta
    mov ah, 0x1A
    int 0x21
    mov dx, fcb
    mov cx, 2
    mov ah, 0x27
    int 0x21
    mov [r_read], al
    mov [r_cnt], cl
    mov dx, fcb
    mov ah, 0x10
    int 0x21
    ; --- handle API: create OUT.TXT, write dta (8 bytes) + results, close
    mov dx, oname
    xor cx, cx
    mov ah, 0x3C
    int 0x21
    jc fail
    mov bx, ax
    mov dx, dta
    mov cx, 12
    mov ah, 0x40
    int 0x21
    ; seek back to 2 and overwrite one byte
    mov ax, 0x4200
    xor cx, cx
    mov dx, 2
    int 0x21
    mov dx, star
    mov cx, 1
    mov ah, 0x40
    int 0x21
    mov ah, 0x3E
    int 0x21
    ; --- memory: shrink, allocate, free
    mov bx, 0x1000
    mov ah, 0x4A
    int 0x21
    jc fail
    mov bx, 0x100
    mov ah, 0x48
    int 0x21
    jc fail
    mov es, ax
    mov ah, 0x49
    int 0x21
    jc fail
    ; --- find first *.BIN
    mov dx, dta
    mov ah, 0x1A
    int 0x21
    mov dx, pat
    mov cx, 0
    mov ah, 0x4E
    int 0x21
    jc fail
    mov dx, okmsg
    mov ah, 9
    int 0x21
    mov ax, 0x4C00
    int 0x21
fail:
    mov ax, 0x4C01
    int 0x21
fname: db "data.bin", 0
oname: db "out.txt", 0
pat:   db "*.BIN", 0
star:  db "*"
okmsg: db "DOS-OK$"
fcb:   times 44 db 0
dta:   times 8 db 0
r_parse: db 0x55
r_open:  db 0x55
r_read:  db 0x55
r_cnt:   db 0x55
       times 64 db 0
"""


def main():
    with tempfile.TemporaryDirectory() as d:
        asm = os.path.join(d, "t.asm")
        open(asm, "w").write(SRC)
        subprocess.run(["nasm", "-f", "bin", "-o", os.path.join(d, "T.COM"), asm], check=True)
        open(os.path.join(d, "DATA.BIN"), "wb").write(b"0123456789AB")
        s = Session(d, "T.COM", "", {"C": d}, "C", quiet=True, log=lambda *_: None)
        s.run(2_000_000)
        fails = []
        if not s.m.exited or s.m.exit_code != 0:
            fails.append("program did not exit cleanly (code %d)" % s.m.exit_code)
        out = open(os.path.join(d, "OUT.TXT"), "rb").read() if os.path.exists(os.path.join(d, "OUT.TXT")) else b""
        want = b"45*789AB" + bytes([0, 0, 0, 2])
        if out != want:
            fails.append("OUT.TXT = %r, want %r" % (out, want))
        if "DOS-OK" not in s.text():
            fails.append("AH=09 message missing")
        if fails:
            print("FAILED:\n  " + "\n  ".join(fails))
            return 1
        print("DOS service test passed (FCB 29/0F/27/10, handles 3C/40/42/3E, memory 4A/48/49, find 4E, print 09)")
        return 0


if __name__ == "__main__":
    sys.exit(main())

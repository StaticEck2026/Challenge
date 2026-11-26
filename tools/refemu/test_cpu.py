#!/usr/bin/env python3
"""CPU unit tests: assemble snippets with nasm, run them, check registers/flags.

Run: python3 -I tools/refemu/test_cpu.py
Expected flag values follow the Intel 8086/80186 documentation; "undefined"
flags are not checked.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from cpu import CPU  # noqa: E402

FLAGS = ("cf", "pf", "af", "zf", "sf", "of", "df")
FAILS = []
COUNT = [0]


def assemble(src):
    with tempfile.TemporaryDirectory() as d:
        asm = os.path.join(d, "t.asm")
        out = os.path.join(d, "t.bin")
        with open(asm, "w") as fp:
            fp.write("bits 16\ncpu 186\norg 0x100\n" + src + "\nhlt\n")
        subprocess.run(["nasm", "-f", "bin", "-o", out, asm], check=True)
        return open(out, "rb").read()


def run(src, **init):
    code = assemble(src)
    mem = bytearray(0x100000 + 16)
    cpu = CPU(mem)
    seg = 0x1000
    mem[(seg << 4) + 0x100:(seg << 4) + 0x100 + len(code)] = code
    for i in range(4):
        cpu.sr[i] = seg
    cpu.ip = 0x100
    cpu.r[4] = 0xFFFE
    names = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
    for k, v in init.items():
        if k in names:
            cpu.r[names.index(k)] = v
        else:
            setattr(cpu, k, v)
    ints = []

    def hle(n):
        ints.append(n)
    cpu.hle = hle
    # INT 0 vector -> a HLT at 0000:0500 so divide errors are observable
    mem[0] = 0x00
    mem[1] = 0x05
    mem[0x500] = 0xF4
    for _ in range(100000):
        cpu.run(1)
        if cpu.halted:
            break
    cpu.mem_ = mem
    cpu.seg_ = seg
    return cpu


def check(name, cpu, **exp):
    COUNT[0] += 1
    names = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
    bad = []
    for k, v in exp.items():
        if k in names:
            got = cpu.r[names.index(k)]
        elif k in ("es", "cs", "ss", "ds"):
            got = cpu.sr[("es", "cs", "ss", "ds").index(k)]
        elif k == "al":
            got = cpu.r[0] & 0xFF
        elif k == "ah":
            got = cpu.r[0] >> 8
        elif k == "ip":
            got = cpu.ip
        else:
            got = getattr(cpu, k)
        if got != v:
            bad.append("%s=%X (want %X)" % (k, got, v))
    if bad:
        FAILS.append("%s: %s" % (name, ", ".join(bad)))


def t(name, src, init=None, **exp):
    check(name, run(src, **(init or {})), **exp)


def main():
    # ---- ADD / ADC
    t("add8 carry", "mov al,0xFF\nadd al,1", al=0, cf=1, zf=1, af=1, of=0, sf=0, pf=1)
    t("add8 overflow", "mov al,0x7F\nadd al,1", al=0x80, cf=0, of=1, sf=1, af=1, zf=0, pf=0)
    t("add16", "mov ax,0x8000\nadd ax,0x8000", ax=0, cf=1, of=1, zf=1)
    t("adc16", "stc\nmov ax,0xFFFF\nadc ax,0", ax=0, cf=1, zf=1, af=1)
    t("adc8 no carry", "clc\nmov al,0x0F\nadc al,1", al=0x10, af=1, cf=0)
    # ---- SUB / SBB / CMP / NEG
    t("sub8 borrow", "mov al,0\nsub al,1", al=0xFF, cf=1, sf=1, af=1, of=0, zf=0)
    t("sub8 overflow", "mov al,0x80\nsub al,1", al=0x7F, cf=0, of=1, sf=0, af=1)
    t("sbb16", "stc\nmov ax,0\nsbb ax,0", ax=0xFFFF, cf=1, sf=1)
    t("sbb16 b", "stc\nmov ax,5\nsbb ax,4", ax=0, cf=0, zf=1)
    t("cmp16 lt", "mov ax,3\ncmp ax,5", ax=3, cf=1, sf=1, zf=0, of=0)
    t("cmp16 signed ovf", "mov ax,0x8000\ncmp ax,1", cf=0, of=1, sf=0)
    t("neg8", "mov al,1\nneg al", al=0xFF, cf=1, sf=1, of=0)
    t("neg8 zero", "mov al,0\nneg al", al=0, cf=0, zf=1)
    t("neg8 0x80", "mov al,0x80\nneg al", al=0x80, cf=1, of=1)
    t("neg16", "mov ax,0x8000\nneg ax", ax=0x8000, of=1, cf=1)
    # ---- INC / DEC keep CF
    t("inc keeps cf", "stc\nmov al,0xFF\ninc al", al=0, cf=1, zf=1, af=1, of=0)
    t("inc16 ovf", "clc\nmov bx,0x7FFF\ninc bx", bx=0x8000, cf=0, of=1, sf=1)
    t("dec8", "stc\nmov cl,0\ndec cl", cx=0xFF, cf=1, sf=1, af=1)
    t("dec16 ovf", "mov dx,0x8000\ndec dx", dx=0x7FFF, of=1)
    # ---- logic
    t("and", "stc\nmov ax,0xF0F0\nand ax,0x0FF0", ax=0x00F0, cf=0, of=0, zf=0, sf=0, pf=1)
    t("xor self", "mov ax,1234\nxor ax,ax", ax=0, zf=1, pf=1, cf=0)
    t("or sign", "mov al,0\nor al,0x80", al=0x80, sf=1, pf=0)
    t("test", "mov al,0x81\ntest al,0x80", al=0x81, zf=0, sf=1)
    t("not", "mov ax,0x00FF\nnot ax", ax=0xFF00)
    # ---- MUL / IMUL
    t("mul8", "mov al,0x80\nmov bl,2\nmul bl", ax=0x100, cf=1, of=1)
    t("mul8 small", "mov al,3\nmov bl,4\nmul bl", ax=12, cf=0, of=0)
    t("mul16", "mov ax,0xFFFF\nmov bx,0xFFFF\nmul bx", ax=1, dx=0xFFFE, cf=1, of=1)
    t("imul8", "mov al,-2\nmov bl,3\nimul bl", ax=0xFFFA, cf=0, of=0)
    t("imul8 ovf", "mov al,100\nmov bl,2\nimul bl", ax=200, cf=1, of=1)
    t("imul16", "mov ax,-300\nmov bx,300\nimul bx", ax=(-90000) & 0xFFFF, dx=((-90000) >> 16) & 0xFFFF, cf=1)
    t("imul imm8", "mov bx,-7\nimul cx,bx,3", cx=(-21) & 0xFFFF, cf=0)
    t("imul imm16", "mov bx,1000\nimul cx,bx,1000", cx=(1000000) & 0xFFFF, cf=1, of=1)
    # ---- DIV / IDIV
    t("div8", "mov ax,1001\nmov bl,10\ndiv bl", al=100, ah=1)
    t("div16", "mov dx,1\nmov ax,0\nmov bx,2\ndiv bx", ax=0x8000, dx=0)
    t("idiv8", "mov ax,-7\nmov bl,2\nidiv bl", al=(-3) & 0xFF, ah=(-1) & 0xFF)
    t("idiv16", "mov ax,-1000\ncwd\nmov bx,7\nidiv bx", ax=(-142) & 0xFFFF, dx=(-6) & 0xFFFF)
    t("div by zero -> int0", "mov ax,5\nmov bl,0\ndiv bl", ip=0x501, cs=0)
    t("div overflow -> int0", "mov ax,0x1000\nmov bl,2\ndiv bl", ip=0x501, cs=0)
    # ---- BCD
    t("daa", "mov al,0x79\nadd al,0x35\ndaa", al=0x14, cf=1, af=1)
    t("daa2", "mov al,0x09\nadd al,0x01\ndaa", al=0x10, cf=0, af=1)
    t("das", "mov al,0x35\nsub al,0x47\ndas", al=0x88, cf=1)
    t("aaa", "mov ax,0x0009\nadd al,3\naaa", ax=0x0102, cf=1, af=1)
    t("aas", "mov ax,0x0202\nsub al,5\naas", ax=0x0107, cf=1, af=1)
    t("aam", "mov al,79\naam", ax=0x0709, zf=0)
    t("aad", "mov ax,0x0709\naad", ax=79)
    t("cbw/cwd", "mov al,0x80\ncbw\ncwd", ax=0xFF80, dx=0xFFFF)
    # ---- shifts / rotates
    t("shl1", "mov al,0x81\nshl al,1", al=0x02, cf=1, of=1)
    t("shl cl", "mov ax,0x0001\nmov cl,15\nshl ax,cl", ax=0x8000, cf=0, sf=1)
    t("shl imm", "mov ax,0x0003\nshl ax,4", ax=0x30)
    t("shr1", "mov al,0x81\nshr al,1", al=0x40, cf=1, of=1)
    t("sar", "mov al,0x81\nsar al,1", al=0xC0, cf=1, of=0)
    t("sar many", "mov ax,0x8000\nmov cl,20\nsar ax,cl", ax=0xFFFF, cf=1)
    t("rol", "mov al,0x81\nrol al,1", al=0x03, cf=1, of=1)
    t("ror", "mov al,0x81\nror al,1", al=0xC0, cf=1, of=0)
    t("rcl", "stc\nmov al,0x80\nrcl al,1", al=0x01, cf=1)
    t("rcr", "clc\nmov al,0x01\nrcr al,1", al=0x00, cf=1)
    t("rcr16 cl", "stc\nmov ax,0x0001\nmov cl,2\nrcr ax,cl", ax=0xC000, cf=0)
    t("shift count 0 keeps flags", "stc\nmov cl,0\nshl ax,cl", cf=1)
    t("shl 8 word", "mov ax,0x1234\nmov cl,8\nshl ax,cl", ax=0x3400, cf=0)
    # ---- string ops
    t("rep movsb", """
        mov si,src
        mov di,dst
        mov cx,4
        cld
        rep movsb
        mov ax,[dst]
        mov bx,[dst+2]
        jmp done
    src: db 1,2,3,4
    dst: db 0,0,0,0
    done:""", ax=0x0201, bx=0x0403, cx=0)
    t("rep movsw back", """
        mov si,src+2
        mov di,dst+2
        mov cx,2
        std
        rep movsw
        cld
        mov ax,[dst]
        mov bx,[dst+2]
        jmp done
    src: dw 0x1111,0x2222
    dst: dw 0,0
    done:""", ax=0x1111, bx=0x2222, cx=0, df=0)
    t("repe cmpsb", """
        mov si,a
        mov di,b
        mov cx,5
        cld
        repe cmpsb
        jmp done
    a: db 'abcde'
    b: db 'abXde'
    done:""", cx=2, zf=0)
    t("repne scasb", """
        mov di,s
        mov al,'c'
        mov cx,10
        cld
        repne scasb
        jmp done
    s: db 'abcdefghij'
    done:""", cx=7, zf=1)
    t("stosw/lodsb", """
        mov di,buf
        mov ax,0xABCD
        mov cx,3
        rep stosw
        mov si,buf+1
        lodsb
        jmp done
    buf: dw 0,0,0,0
    done:""", al=0xAB, cx=0)
    t("movsb overlap fill", """
        mov byte [buf],7
        mov si,buf
        mov di,buf+1
        mov cx,5
        cld
        rep movsb
        mov al,[buf+5]
        jmp done
    buf: db 0,0,0,0,0,0,0
    done:""", al=7)
    # ---- stack / 186
    t("pusha/popa", "mov ax,1\nmov cx,2\nmov dx,3\nmov bx,4\nmov bp,5\nmov si,6\nmov di,7\npusha\n"
      "xor ax,ax\nxor cx,cx\nxor dx,dx\nxor bx,bx\nxor bp,bp\nxor si,si\nxor di,di\npopa",
      ax=1, cx=2, dx=3, bx=4, bp=5, si=6, di=7, sp=0xFFFE)
    t("enter/leave", "mov bp,0x1234\nenter 8,0\nmov ax,bp\nleave", ax=0xFFFC, bp=0x1234, sp=0xFFFE)
    t("enter level1", "mov bp,0x1234\nenter 4,1\nmov ax,sp\nmov bx,bp\nleave", ax=0xFFF6, bx=0xFFFC, bp=0x1234)
    t("push imm", "push word 0x1234\npush byte -1\npop bx\npop ax", ax=0x1234, bx=0xFFFF)
    t("push sp (8086 style)", "push sp\npop ax", ax=0xFFFC)
    t("pushf/popf", "stc\npushf\nclc\npopf", cf=1)
    t("lahf/sahf", "stc\nmov ah,0\nlahf\nclc\nsahf", cf=1)
    t("xchg", "mov ax,1\nmov bx,2\nxchg ax,bx", ax=2, bx=1)
    t("xlat", "mov bx,tab\nmov al,2\nxlatb\njmp done\ntab: db 10,20,30\ndone:", al=30)
    t("lea", "mov bx,0x10\nmov si,0x20\nlea ax,[bx+si+5]", ax=0x35)
    t("les", "les di,[p]\njmp done\np: dw 0x1234,0x5678\ndone:", di=0x1234, es=0x5678)
    t("loop", "mov cx,5\nxor ax,ax\nl: inc ax\nloop l", ax=5, cx=0)
    t("jcxz", "xor cx,cx\nmov ax,1\njcxz z\nmov ax,2\nz:", ax=1)
    t("call/ret", "call f\njmp done\nf: mov ax,9\nret\ndone:", ax=9, sp=0xFFFE)
    t("ret imm", "push ax\ncall f\njmp done\nf: ret 2\ndone:", sp=0xFFFE)
    t("jl/jg signed", "mov ax,-1\ncmp ax,1\nmov bx,0\njl a\nmov bx,1\na:", bx=0)
    t("ja unsigned", "mov ax,-1\ncmp ax,1\nmov bx,0\nja a\nmov bx,1\na:", bx=0)
    t("bound ok", "mov ax,5\nbound ax,[b]\njmp done\nb: dw 0,10\ndone:", ax=5)
    t("grp1 sign-ext", "mov ax,10\nsub ax,byte -1", ax=11)
    t("mov seg", "mov ax,0x2000\nmov es,ax\nmov bx,es", bx=0x2000)
    t("segment override", "mov ax,0x1000\nmov es,ax\nmov word [es:0x200],0x4321\nmov ax,[0x200]", ax=0x4321)
    t("salc-free cmc", "stc\ncmc", cf=0)
    total = COUNT[0]
    if FAILS:
        print("FAILED %d/%d" % (len(FAILS), total))
        for f in FAILS:
            print("  " + f)
        return 1
    print("all %d CPU tests passed" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Command-line runner for the reference emulator.

Example:
  python3 -I tools/refemu/run.py --root build/nflrun --program NFL.EXE \
      --keys "{when:CONFIGURATION}{shot:config}..." --max-minstr 50 --text-dump

Key script tokens:
  plain characters          typed as keys (upper case / symbols use Shift)
  {Enter} {Esc} {Tab} {BS} {Space} {Up} {Down} {Left} {Right} {PgUp} {PgDn}
  {Home} {End} {Ins} {Del} {F1}..{F12}
  {Alt-X} {Ctrl-X} {Shift-X}  modifier combinations (X = letter, digit or key name)
  {wait:N}                  wait N timer ticks (18.2 per emulated second)
  {when:TEXT}               wait until TEXT appears on the text screen
  {gone:TEXT}               wait until TEXT is no longer on screen
  {shot:NAME}               save NAME.png and NAME.txt in --shots dir
  {mark:NAME}               print a progress marker
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from machine import Machine          # noqa: E402
from bios import BIOS                # noqa: E402
from dos import DOS                  # noqa: E402
import video                         # noqa: E402
from font_cp437 import FONT8, FONT16  # noqa: E402

NAMED = {
    "Esc": 0x01, "BS": 0x0E, "Tab": 0x0F, "Enter": 0x1C, "Space": 0x39,
    "F1": 0x3B, "F2": 0x3C, "F3": 0x3D, "F4": 0x3E, "F5": 0x3F, "F6": 0x40,
    "F7": 0x41, "F8": 0x42, "F9": 0x43, "F10": 0x44, "F11": 0x57, "F12": 0x58,
    "Home": 0x47, "Up": 0x48, "PgUp": 0x49, "Left": 0x4B, "Center": 0x4C, "Right": 0x4D,
    "End": 0x4F, "Down": 0x50, "PgDn": 0x51, "Ins": 0x52, "Del": 0x53,
    "KP-": 0x4A, "KP+": 0x4E, "KP*": 0x37,
}
LSHIFT, CTRL, ALT = 0x2A, 0x1D, 0x38

CHARS = {}


def _build_chars():
    rows = [(0x02, "1234567890-=", "!@#$%^&*()_+"), (0x10, "qwertyuiop[]", "QWERTYUIOP{}"),
            (0x1E, "asdfghjkl;'`", 'ASDFGHJKL:"~'), (0x2B, "\\zxcvbnm,./", "|ZXCVBNM<>?")]
    for base, norm, shift in rows:
        for i, ch in enumerate(norm):
            CHARS[ch] = (base + i, False)
        for i, ch in enumerate(shift):
            CHARS[ch] = (base + i, True)
    CHARS[" "] = (0x39, False)
    CHARS["\n"] = (0x1C, False)
    CHARS["\r"] = (0x1C, False)


_build_chars()


def key_codes(sc, mods=()):
    """Scancode sequence: modifier makes, key make, key break, modifier breaks."""
    seq = list(mods) + [sc, sc | 0x80] + [m | 0x80 for m in reversed(mods)]
    return seq


def parse_keys(script):
    """Return a list of actions: ('key', [scancodes]) / ('wait', n) / ('when', text) / ..."""
    out = []
    i = 0
    while i < len(script):
        c = script[i]
        if c == "{":
            j = script.index("}", i)
            tok = script[i + 1:j]
            i = j + 1
            if ":" in tok and tok.split(":", 1)[0] in ("wait", "when", "gone", "shot", "mark", "tickrate"):
                k, v = tok.split(":", 1)
                out.append((k, int(v) if k in ("wait", "tickrate") else v))
                continue
            mods = []
            name = tok
            while "-" in name and name.split("-", 1)[0] in ("Alt", "Ctrl", "Shift"):
                mod, name = name.split("-", 1)
                mods.append({"Alt": ALT, "Ctrl": CTRL, "Shift": LSHIFT}[mod])
            if name in NAMED:
                sc = NAMED[name]
            elif len(name) == 1 and name.lower() in CHARS:
                sc, sh = CHARS[name.lower()] if name.isalpha() else CHARS[name]
                if sh and LSHIFT not in mods:
                    mods.append(LSHIFT)
            elif name == "{":
                sc, _ = CHARS["["]
                mods.append(LSHIFT)
            else:
                raise ValueError("unknown key token {%s}" % tok)
            out.append(("key", key_codes(sc, mods)))
        else:
            i += 1
            if c not in CHARS:
                raise ValueError("cannot type %r" % c)
            sc, sh = CHARS[c]
            out.append(("key", key_codes(sc, [LSHIFT] if sh else [])))
    return out


class Session:
    def __init__(self, root, program="NFL.EXE", args="", drives=None, cur_drive="C",
                 ipt=20000, shots="build/shots", log=None, printer=None, quiet=False):
        self.logf = log or (lambda s: print(s, file=sys.stderr))
        self.m = Machine(ipt=ipt, log=self.logf)
        self.bios = BIOS(self.m, FONT8, printer_path=printer)
        if drives is None:
            drives = {"A": root, "B": root, "C": root}
        self.dos = DOS(self.m, self.bios, drives, cur_drive=cur_drive, log=self.logf,
                       printer_path=printer)
        self.info = self.dos.load_program("%s:%s" % (cur_drive, program), args)
        self.shots = shots
        self.actions = []
        self.ai = 0
        self.wait_ticks = 0
        self.when_since = 0
        self.quiet = quiet
        self.m.tick_hooks.append(self.on_tick)

    def text(self):
        return video.text_dump(self.m)

    def shot(self, name):
        os.makedirs(self.shots, exist_ok=True)
        p = os.path.join(self.shots, name + ".png")
        video.screenshot(self.m, p, FONT8, FONT16)
        with open(os.path.join(self.shots, name + ".txt"), "w") as fp:
            fp.write(self.text() + "\n")
        self.logf("[shot] %s (tick %d, %d instr)" % (p, self.m.ticks, self.m.cpu.icount))
        return p

    def on_tick(self):
        while self.ai < len(self.actions):
            kind, v = self.actions[self.ai]
            if kind == "wait":
                if self.wait_ticks == 0:
                    self.wait_ticks = v
                self.wait_ticks -= 1
                if self.wait_ticks > 0:
                    return
                self.ai += 1
                continue
            if kind == "when" or kind == "gone":
                if self.m.ticks % 4:
                    return
                present = v in self.text()
                if present != (kind == "when"):
                    return
                if not self.quiet:
                    self.logf("[%s] %r at tick %d" % (kind, v, self.m.ticks))
                self.ai += 1
                continue
            if kind == "shot":
                self.shot(v)
                self.ai += 1
                continue
            if kind == "mark":
                self.logf("[mark] %s tick %d instr %d" % (v, self.m.ticks, self.m.cpu.icount))
                self.ai += 1
                continue
            if kind == "tickrate":
                self.m.ipt = v
                self.ai += 1
                continue
            if kind == "key":
                if self.m.kbd_queue or not self.m.bios_kbd_empty():
                    return
                self.m.key_scancodes(v)
                self.ai += 1
                return
            self.ai += 1

    def run(self, max_instr, until_text=None, progress=0):
        m = self.m
        t0 = time.time()
        i0 = m.cpu.icount
        end = i0 + max_instr
        chunk = 1_000_000
        last_report = time.time()
        while not m.exited and m.cpu.icount < end:
            m.run(min(chunk, end - m.cpu.icount))
            if until_text and until_text in self.text():
                break
            if progress and time.time() - last_report > progress:
                last_report = time.time()
                dt = time.time() - t0
                self.logf("[progress] %.1fM emulated, %.1fM executed, %d ticks, %.0f exec instr/s, action %d/%d" % (
                    m.cpu.icount / 1e6, m.cpu.executed / 1e6, m.ticks, m.cpu.executed / max(dt, 1e-6),
                    self.ai, len(self.actions)))
        dt = time.time() - t0
        self.executed = m.cpu.executed
        return m.cpu.icount - i0, dt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="build/nflrun", help="writable copy of the game folder")
    ap.add_argument("--program", default="NFL.EXE")
    ap.add_argument("--args", default="", help="command tail, e.g. '/s'")
    ap.add_argument("--drive", action="append", default=[], help="X=hostdir (repeatable)")
    ap.add_argument("--cur-drive", default="C")
    ap.add_argument("--keys", default="", help="key script (see module doc)")
    ap.add_argument("--keys-file", help="read the key script from a file")
    ap.add_argument("--max-minstr", type=float, default=50, help="instruction budget in millions")
    ap.add_argument("--ipt", type=int, default=20000, help="instructions per 18.2 Hz timer tick")
    ap.add_argument("--until-text", help="stop when this text appears")
    ap.add_argument("--screenshot", help="final screenshot path (.png)")
    ap.add_argument("--shots", default="build/shots")
    ap.add_argument("--text-dump", action="store_true", help="print the final text screen")
    ap.add_argument("--trace-ints", action="store_true", help="count every INT/AH and port used")
    ap.add_argument("--trace", type=int, default=0, help="keep a ring buffer of the last N CS:IP")
    ap.add_argument("--printer", help="file that receives printer output")
    ap.add_argument("--progress", type=float, default=0, help="progress report interval (s)")
    ap.add_argument("--stdout", action="store_true", help="echo DOS console output to stdout")
    a = ap.parse_args(argv)

    drives = {"A": a.root, "B": a.root, "C": a.root}
    for d in a.drive:
        k, v = d.split("=", 1)
        drives[k.upper()] = v
    s = Session(a.root, a.program, a.args, drives, a.cur_drive, a.ipt, a.shots, printer=a.printer)
    if a.stdout:
        s.dos.echo_stdout = lambda c: (sys.stdout.write(chr(c)), sys.stdout.flush())
    script = a.keys
    if a.keys_file:
        script += open(a.keys_file).read().replace("\n", "")
    s.actions = parse_keys(script)
    if a.trace:
        s.m.cpu.trace = [0] * a.trace
        s.m.cpu.trace_len = a.trace
    if a.trace_ints:
        s.m.trace_ints = True

        def hook(n):
            if s.m.cpu.op_cs == 0xF000:
                return
            key = ("INT", n, s.m.cpu.r[0] >> 8)
            s.m.int_counts[key] = s.m.int_counts.get(key, 0) + 1
        s.m.cpu.int_hook = hook
    print("[load] %s" % s.info, file=sys.stderr)
    n, dt = s.run(int(a.max_minstr * 1e6), a.until_text, a.progress)
    print("[done] %d emulated-time instructions, %d executed in %.1fs = %.0f executed instr/s; ticks=%d exited=%s code=%d; actions %d/%d" % (
        n, s.executed, dt, s.executed / max(dt, 1e-6), s.m.ticks, s.m.exited, s.m.exit_code, s.ai, len(s.actions)),
        file=sys.stderr)
    cpu = s.m.cpu
    print("[cpu] CS:IP=%04X:%04X AX=%04X BX=%04X CX=%04X DX=%04X SI=%04X DI=%04X BP=%04X SP=%04X DS=%04X ES=%04X SS=%04X" % (
        cpu.sr[1], cpu.ip, cpu.r[0], cpu.r[3], cpu.r[1], cpu.r[2], cpu.r[6], cpu.r[7], cpu.r[5], cpu.r[4],
        cpu.sr[3], cpu.sr[0], cpu.sr[2]), file=sys.stderr)
    if a.trace:
        t = cpu.trace
        p = cpu.trace_pos
        seq = t[p:] + t[:p]
        print("[trace] " + " ".join("%04X:%04X" % (v >> 16, v & 0xFFFF) for v in seq if v), file=sys.stderr)
    if a.trace_ints:
        print("[ints] software INT instructions executed (vector/AH: count):", file=sys.stderr)
        for k in sorted(k for k in s.m.int_counts if k[0] == "INT"):
            print("   INT %02Xh AH=%02Xh: %d" % (k[1], k[2], s.m.int_counts[k]), file=sys.stderr)
        print("[ints] services reached in the emulated BIOS/DOS (vector/AH: count):", file=sys.stderr)
        for k in sorted(k for k in s.m.int_counts if k[0] != "INT"):
            print("   %02Xh/%02Xh: %d" % (k[0], k[1], s.m.int_counts[k]), file=sys.stderr)
        print("[ports] " + ", ".join("%s %03Xh x%d" % (k[0], k[1], v) for k, v in sorted(s.m.port_counts.items())),
              file=sys.stderr)
        print("[files] " + "; ".join("%s %s" % (op, name) for op, name, _ in s.dos.file_log), file=sys.stderr)
    if a.screenshot:
        os.makedirs(os.path.dirname(os.path.abspath(a.screenshot)), exist_ok=True)
        video.screenshot(s.m, a.screenshot, FONT8, FONT16)
        print("[shot] %s" % a.screenshot, file=sys.stderr)
    if a.text_dump:
        print(s.text())
    return 0


if __name__ == "__main__":
    sys.exit(main())

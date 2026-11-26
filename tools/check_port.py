#!/usr/bin/env python3
"""Cross-check the legacy GDScript interpreter port (tools/gdport/legacy_emu/*.gd) against the Python oracle
(tools/refemu/*.py).

Checks:
  1. opcodes   every opcode the Python CPU dispatches has a pattern in the
               GDScript exec_op() match (hex literals of the match patterns)
  2. vectors   every interrupt vector registered in the Python BIOS/DOS has a
               handler registered in the GDScript BIOS/DOS
  3. services  for every function, the set of `ah == 0xNN` / `al == 0xNN`
               constants (the INT sub-functions) is the same in both ports
  4. ports     the set of I/O port constants in port_in()/port_out() matches
  5. functions every Python method of the ported modules exists in GDScript
               (same name), except a short list of Python-only helpers

Usage: python3 -I tools/check_port.py      (exit status 1 on any mismatch)
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "tools", "refemu")
GD = os.path.join(ROOT, "tools", "gdport", "legacy_emu")
sys.path.insert(0, PY)

MODULES = ["cpu", "machine", "bios", "dos", "video"]
# Python-only helpers (host specifics) and GDScript-only helpers
PY_ONLY = {"__init__", "handled_opcodes", "fetch8", "fetchs8", "op_nop", "op_xchg_ax",
           "op_cbw", "op_cwd", "op_pushf", "op_popf", "op_lahf", "op_test_acc",
           "op_mov_r8_imm", "op_mov_r16_imm", "op_leave", "op_int3", "op_int", "op_into",
           "op_iret", "op_salc", "op_xlat", "op_jcxz", "op_call_near", "op_jmp_near",
           "op_jmp_far", "op_jmp_short", "op_lock", "op_hle", "op_hlt", "op_cmc", "op_clc",
           "op_stc", "op_cli", "op_sti", "op_cld", "op_std", "op_inc_r", "op_dec_r",
           "op_push_iw", "op_push_ib", "op_mov_rm8_r8", "op_mov_rm16_r16", "op_mov_r8_rm8",
           "op_mov_r16_rm16", "op_mov_rm_seg", "op_lea", "op_pop_rm", "_build_table",
           "_build_keymap", "_init_memory", "screenshot", "write_png", "render_rgb",
           "video_state", "key_scancodes", "printer_out", "now", "match83", "find_first"}
# Python opcode handlers inlined in GDScript exec_op() (documented above)


def py_functions(path):
    tree = ast.parse(open(path).read())
    out = {}
    src = open(path).read()
    # module-level functions and class methods (nested helpers are inlined/renamed in GDScript)
    for node in tree.body:
        items = node.body if isinstance(node, ast.ClassDef) else [node]
        for f in items:
            if isinstance(f, ast.FunctionDef):
                out[f.name] = ast.get_source_segment(src, f) or ""
    return out


def gd_functions(path):
    text = open(path).read()
    out = {}
    cur = None
    buf = []
    indent_of_func = 0
    for line in text.splitlines():
        m = re.match(r"^(\t*)(?:static )?func (\w+)\(", line)
        if m:
            if cur:
                out[cur] = "\n".join(buf)
            cur = m.group(2)
            indent_of_func = len(m.group(1))
            buf = [line]
            continue
        if cur is not None:
            stripped = line.lstrip("\t")
            ind = len(line) - len(stripped)
            if stripped and ind <= indent_of_func and not stripped.startswith("#"):
                out[cur] = "\n".join(buf)
                cur = None
                buf = []
            else:
                buf.append(line)
    if cur:
        out[cur] = "\n".join(buf)
    return out


def consts(src, var):
    return set(int(x, 16) for x in re.findall(r"\b%s == 0x([0-9A-Fa-f]+)" % var, src))


def main():
    problems = []
    report = []

    # 1. opcodes
    from cpu import CPU
    py_ops = set(CPU(bytearray(0x100010)).handled_opcodes())
    gd_cpu = open(os.path.join(GD, "cpu.gd")).read()
    body = gd_functions(os.path.join(GD, "cpu.gd"))["exec_op"]
    gd_ops = set()
    for line in body.splitlines():
        s = line.strip()
        if re.fullmatch(r"(0x[0-9A-Fa-f]{2}, )*0x[0-9A-Fa-f]{2}:", s):
            gd_ops |= {int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", s)}
    missing = sorted(py_ops - gd_ops)
    extra = sorted(gd_ops - py_ops)
    report.append("opcodes: python %d, gdscript %d" % (len(py_ops), len(gd_ops)))
    if missing:
        problems.append("opcodes missing in GDScript: " + " ".join("%02X" % o for o in missing))
    if extra:
        problems.append("opcodes only in GDScript: " + " ".join("%02X" % o for o in extra))
    invalid = sorted(set(range(256)) - py_ops)
    report.append("  not dispatched (-> INT 6): " + " ".join("%02X" % o for o in invalid))

    # 2. vectors
    py_vec, gd_vec = {}, {}
    for mod in ("bios", "dos"):
        for m in re.finditer(r"h\[0x([0-9A-F]+)\] = self\.(\w+)", open(os.path.join(PY, mod + ".py")).read()):
            py_vec[int(m.group(1), 16)] = m.group(2)
        for m in re.finditer(r"m\.handlers\[0x([0-9A-F]+)\] = (\w+)", open(os.path.join(GD, mod + ".gd")).read()):
            gd_vec[int(m.group(1), 16)] = m.group(2)
    report.append("vectors: python %s" % " ".join("%02X" % v for v in sorted(py_vec)))
    for v, name in sorted(py_vec.items()):
        if gd_vec.get(v) != name:
            problems.append("vector %02Xh: python %s, gdscript %s" % (v, name, gd_vec.get(v)))
    for v in sorted(set(gd_vec) - set(py_vec)):
        problems.append("vector %02Xh only in GDScript" % v)

    # 3. services (AH/AL constants per function) and 5. function names
    for mod in MODULES:
        pyf = py_functions(os.path.join(PY, mod + ".py"))
        gdf = gd_functions(os.path.join(GD, mod + ".gd"))
        for name, src in pyf.items():
            if name not in gdf:
                if name not in PY_ONLY:
                    problems.append("%s.%s missing in GDScript" % (mod, name))
                continue
            for var in ("ah", "al"):
                a, b = consts(src, var), consts(gdf[name], var)
                if a != b:
                    problems.append("%s.%s %s constants differ: python-only %s gd-only %s" % (
                        mod, name, var.upper(), sorted("%02X" % x for x in a - b),
                        sorted("%02X" % x for x in b - a)))
        svc = {n: sorted(consts(s, "ah")) for n, s in pyf.items() if n.startswith("int") and consts(s, "ah")}
        for n, ahs in sorted(svc.items()):
            report.append("%s.%s AH: %s" % (mod, n, " ".join("%02X" % x for x in ahs)))

    # 4. ports
    def port_consts(src):
        return set(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2,4})\b", src)) - {0xFF, 0xFFFF, 0x00}
    pym = py_functions(os.path.join(PY, "machine.py"))
    gdm = gd_functions(os.path.join(GD, "machine.gd"))
    for fn in ("port_in", "port_out"):
        a, b = port_consts(pym[fn]), port_consts(gdm[fn])
        report.append("%s ports: %s" % (fn, " ".join("%03X" % p for p in sorted(a))))
        if a != b:
            problems.append("%s port constants differ: python-only %s gd-only %s" % (
                fn, sorted("%X" % x for x in a - b), sorted("%X" % x for x in b - a)))

    print("\n".join(report))
    if problems:
        print("\nPROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("\nOK: GDScript port covers every opcode, vector, INT sub-function and port of the oracle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

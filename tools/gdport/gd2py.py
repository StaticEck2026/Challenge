#!/usr/bin/env python3
"""Transpile the GDScript subset used by godot/host/*.gd, the generated
godot/recomp/*.gd and tools/gdport/legacy_emu/*.gd into one Python module.

This exists only to *test* the GDScript port without a Godot binary: the
generated module runs on tools/gdport/gdrt.py (a shim of the Godot API calls
the emulator uses).  It handles: class_name/extends, inner classes, const/var
members, (static) funcs with typed params/defaults, var declarations, if/elif/
else/while/for, match (-> Python match/case), member access without `self`,
`X.new()`, builtin String/Array/Dictionary/Packed*Array methods (-> gdcall),
`"fmt" % [..]`, integer `/`, lambdas `func(a, b): return e`, and `as T` casts.

Usage: python3 tools/gdport/gd2py.py out.py godot/host/machine.gd godot/recomp/u_r_0.gd ...
A file without class_name becomes a class named after the file; `extends X` of a
translated class X becomes Python inheritance.
"""
import re
import sys

BUILTIN_METHODS = {
    "begins_with", "ends_with", "to_upper", "to_lower", "length", "substr", "find", "split",
    "rpad", "unicode_at", "to_ascii_buffer", "strip_edges", "repeat", "get_file", "replace",
    "join", "hex_decode", "is_empty", "size", "resize", "fill", "append", "append_array",
    "slice", "decode_u16", "decode_u32", "remove_at", "duplicate", "get_string_from_ascii", "hex_encode",
    "pop_back", "sort_custom", "has", "get", "erase", "merge", "keys", "call"}
RENAMES = {"true": "True", "false": "False", "null": "None", "int": "gdint", "str": "gd_str"}
TOKEN_RE = re.compile(r'''
    (?P<str>"(?:\\.|[^"\\])*")
  | (?P<num>0x[0-9A-Fa-f]+|\d+\.\d*(?:e[+-]?\d+)?|\d+e[+-]?\d+|\d+)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>:=|<<=|>>=|\*\*|==|!=|<=|>=|<<|>>|\+=|-=|\*=|/=|%=|&=|\|=|\^=|->|[-+*/%<>=!&|^~()\[\]{},.:@])
  | (?P<ws>\s+)
''', re.X)


def tokenize(s):
    out = []
    pos = 0
    while pos < len(s):
        m = TOKEN_RE.match(s, pos)
        if not m:
            raise SyntaxError("cannot tokenize: %r" % s[pos:pos + 30])
        pos = m.end()
        if m.lastgroup != "ws":
            out.append(m.group())
    return out


def strip_comment(line):
    out = []
    in_str = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            out.append(c)
            if c == "\\":
                out.append(line[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == "#":
                break
            if c == '"':
                in_str = True
            out.append(c)
        i += 1
    return "".join(out).rstrip()


def logical_lines(text):
    """Yield (indent, text) joining bracket continuations."""
    buf = None
    indent = 0
    depth = 0
    for raw in text.splitlines():
        line = strip_comment(raw)
        if not line.strip():
            continue
        if buf is None:
            indent = len(line) - len(line.lstrip("\t"))
            buf = line.strip()
        else:
            buf += " " + line.strip()
        depth = 0
        for t in tokenize(buf):
            if t in "([{":
                depth += 1
            elif t in ")]}":
                depth -= 1
        if depth <= 0:
            yield indent, buf
            buf = None


class ClassInfo:
    def __init__(self, name):
        self.name = name
        self.base = None    # name of a translated base class (``extends X``)
        self.consts = []    # (name, expr)
        self.vars = []      # (name, type, expr)
        self.funcs = []     # (name, params, static, body[(indent, text)])

    def members(self):
        return {v[0] for v in self.vars} | {f[0] for f in self.funcs if not f[2]}

    def statics(self):
        return {f[0] for f in self.funcs if f[2]}


def parse_file(path):
    import os
    lines = list(logical_lines(open(path).read()))
    stem = os.path.splitext(os.path.basename(path))[0]
    outer = None
    classes = []
    cur = None
    base_indent = 0
    i = 0
    while i < len(lines):
        ind, text = lines[i]
        if ind == 0 and text.startswith("class_name "):
            outer = ClassInfo(text.split()[1])
            classes.append(outer)
            cur = outer
            base_indent = 0
            i += 1
            continue
        if ind == 0 and text.startswith("class "):
            cur = ClassInfo(text[6:].rstrip(":").strip())
            classes.append(cur)
            base_indent = 1
            i += 1
            continue
        if ind == 0 and cur is not outer and cur is not None and not text.startswith("class"):
            cur = outer
            base_indent = 0
        if cur is None:
            cur = outer = ClassInfo(stem)
            classes.append(cur)
        if text.startswith("extends "):
            base = text.split()[1]
            if base not in ("RefCounted", "Node", "Object", "Control", "Resource") and not base.startswith('"'):
                cur.base = base
            i += 1
            continue
        if ind == base_indent and text.startswith("const "):
            m = re.match(r"const (\w+)\s*(?::\s*\w+)?\s*:?=\s*(.*)$", text)
            cur.consts.append((m.group(1), m.group(2)))
            i += 1
            continue
        if ind == base_indent and text.startswith("var "):
            m = re.match(r"var (\w+)\s*(?::\s*([\w\[\]]+))?\s*(?::?=\s*(.*))?$", text)
            cur.vars.append((m.group(1), m.group(2), m.group(3)))
            i += 1
            continue
        if ind == base_indent and (text.startswith("func ") or text.startswith("static func ")):
            static = text.startswith("static ")
            m = re.match(r"(?:static )?func (\w+)\((.*)\)\s*(?:->\s*[\w\[\]]+)?\s*:$", text)
            name, params = m.group(1), m.group(2)
            body = []
            i += 1
            while i < len(lines) and lines[i][0] > base_indent:
                body.append((lines[i][0] - base_indent - 1, lines[i][1]))
                i += 1
            cur.funcs.append((name, params, static, body))
            continue
        if ind == base_indent and text.startswith("@"):
            i += 1
            continue
        raise SyntaxError("%s: unexpected top-level line: %s" % (path, text))
    return classes


def split_top(s, sep=","):
    out, depth, cur = [], 0, ""
    for t in tokenize(s):
        if t in "([{":
            depth += 1
        elif t in ")]}":
            depth -= 1
        if t == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += (" " if cur else "") + t
    if cur:
        out.append(cur)
    return out


class Translator:
    def __init__(self, cls, all_classes):
        self.cls = cls
        self.members = cls.members()
        by_name = {c.name: c for c in all_classes}
        b = cls.base
        while b in by_name:
            self.members |= by_name[b].members()
            b = by_name[b].base
        self.statics = cls.statics()
        self.consts = {c[0] for c in cls.consts}
        self.class_names = {c.name for c in all_classes}

    def expr(self, s, local, static=False):
        toks = tokenize(s)
        out = []
        i = 0
        while i < len(toks):
            t = toks[i]
            prev = out[-1] if out else None
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if t == "func" and nxt == "(":
                # lambda: func(a, b): return expr
                j = toks.index(")", i)
                params = [p for p in toks[i + 2:j] if p != ","]
                assert toks[j + 1] == ":" and toks[j + 2] == "return"
                out += ["lambda", ", ".join(params), ":"]
                local = set(local) | set(params)
                i = j + 3
                continue
            if t == "as" and prev not in (".",):
                i += 2          # drop "as Type"
                continue
            if t == "." and nxt == "new" and i + 2 < len(toks) and toks[i + 2] == "(":
                i += 2
                continue
            if t == "." and nxt in BUILTIN_METHODS and i + 2 < len(toks) and toks[i + 2] == "(":
                start = self.primary_start(out)
                obj = out[start:]
                del out[start:]
                empty = i + 3 < len(toks) and toks[i + 3] == ")"
                out += ["gdcall", "("] + obj + [",", '"%s"' % nxt] + ([] if empty else [","])
                i += 3
                continue
            if t == "%" and nxt == "[":
                # "fmt" % [a, b]  ->  "fmt" % tuple([a, b])
                depth = 0
                j = i + 1
                while True:
                    if toks[j] in "([{":
                        depth += 1
                    elif toks[j] in ")]}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                inner = self.expr(" ".join(toks[i + 1:j + 1]), local, static)
                out += ["%", "tuple", "(", inner, ")"]
                i = j + 1
                continue
            if t == "/":
                floaty = (nxt and re.fullmatch(r"\d+\.\d*(e[+-]?\d+)?|\d+e[+-]?\d+", nxt)) or (
                    prev and re.fullmatch(r"\d+\.\d*(e[+-]?\d+)?", prev))
                out.append("/" if floaty else "//")
                i += 1
                continue
            if re.fullmatch(r"[A-Za-z_]\w*", t) and prev != ".":
                if t in local:
                    out.append(t)
                elif t in self.members and not static:
                    out += ["self", ".", t]
                elif t in self.statics:
                    out += [self.cls.name, ".", t]
                elif t in RENAMES and not (nxt == "=" and False):
                    out.append(RENAMES[t])
                else:
                    out.append(t)
                i += 1
                continue
            out.append(t)
            i += 1
        return self.join(out)

    @staticmethod
    def primary_start(out):
        j = len(out)
        while j > 0:
            t = out[j - 1]
            if t in (")", "]"):
                depth = 0
                k = j - 1
                while k >= 0:
                    if out[k] in (")", "]", "}"):
                        depth += 1
                    elif out[k] in ("(", "[", "{"):
                        depth -= 1
                        if depth == 0:
                            break
                    k -= 1
                j = k
                # a call or subscription: include the callee
                continue
            if re.fullmatch(r"[A-Za-z_]\w*|\d\w*|\"(?:\\.|[^\"\\])*\"", t) and t not in (
                    "and", "or", "not", "in", "if", "else", "return", "lambda"):
                j -= 1
                if j > 0 and out[j - 1] == ".":
                    j -= 1
                    continue
                break
            break
        return j

    @staticmethod
    def join(toks):
        s = ""
        for t in toks:
            if not s:
                s = t
            elif re.fullmatch(r"\w", s[-1]) and re.match(r"[\w\"]", t):
                s += " " + t
            elif t in ("and", "or", "not", "in", "if", "else", "lambda", "is") or s.endswith(
                    (" and", " or", " not", " in", " if", " else", " lambda", " is")):
                s += " " + t
            elif s[-1] == '"' and re.match(r"\w", t):
                s += " " + t
            else:
                s += t
        return s

    def func(self, name, params, static, body):
        local = set()
        plist = []
        for p in split_top(params):
            toks = tokenize(p)
            pname = toks[0]
            local.add(pname)
            if "=" in toks:
                plist.append("%s=%s" % (pname, self.expr(" ".join(toks[toks.index("=") + 1:]), set())))
            else:
                plist.append(pname)
        pyname = "__gd_init__" if name == "_init" else name
        head = ("    @staticmethod\n    def %s(%s):" % (pyname, ", ".join(plist)) if static
                else "    def %s(%s):" % (pyname, ", ".join(["self"] + plist)))
        lines = [head]
        match_stack = []
        for ind, text in body:
            while match_stack and ind <= match_stack[-1]:
                match_stack.pop()
            pad = "    " * (ind + 2)
            if match_stack and ind == match_stack[-1] + 1:
                pats = text[:-1]
                if pats.strip() == "_":
                    lines.append(pad + "case _:")
                else:
                    lines.append(pad + "case %s:" % " | ".join(p.strip() for p in pats.split(",")))
                continue
            lines.append(pad + self.stmt(text, local, static, match_stack, ind))
        if len(lines) == 1:
            lines.append("        pass")
        return "\n".join(lines)

    def stmt(self, text, local, static, match_stack, ind):
        m = re.match(r"var (\w+)\s*(?::\s*([\w\[\]]+))?\s*(:?=\s*(.*))?$", text)
        if m:
            name, typ, init = m.group(1), m.group(2), m.group(4)
            value = self.expr(init, local, static) if init is not None else default_for(typ)
            local.add(name)
            return "%s = %s" % (name, value)
        m = re.match(r"for (\w+) in (.*):$", text)
        if m:
            local.add(m.group(1))
            return "for %s in %s:" % (m.group(1), self.expr(m.group(2), local, static))
        m = re.match(r"match (.*):$", text)
        if m:
            match_stack.append(ind)
            return "match %s:" % self.expr(m.group(1), local, static)
        m = re.match(r"(if|elif|while) (.*):$", text)
        if m:
            return "%s %s:" % (m.group(1), self.expr(m.group(2), local, static))
        if text in ("else:", "pass", "break", "continue", "return"):
            return text
        if text.startswith("return "):
            return "return " + self.expr(text[7:], local, static)
        return self.expr(text, local, static)


def default_for(typ):
    return {"int": "0", "float": "0.0", "bool": "False", "String": '""'}.get(typ, "None")


def translate(classes_by_file):
    all_classes = [c for cs in classes_by_file for c in cs]
    out = ["# Generated by tools/gdport/gd2py.py -- do not edit", "from gdrt import *  # noqa", ""]
    # consts as module globals first (they may be used bare by other classes' code)
    for c in all_classes:
        tr = Translator(c, all_classes)
        for name, e in c.consts:
            out.append("%s = %s" % (name, tr.expr(e, set(), True)))
    out.append("")
    # base classes first
    names = {c.name for c in all_classes}
    ordered, done = [], set()

    def visit(c):
        if c.name in done:
            return
        if c.base in names:
            visit(next(x for x in all_classes if x.name == c.base))
        done.add(c.name)
        ordered.append(c)
    for c in all_classes:
        visit(c)
    for c in ordered:
        tr = Translator(c, all_classes)
        out.append("class %s%s:" % (c.name, "(%s)" % c.base if c.base in names else ""))
        for name, e in c.consts:
            out.append("    %s = %s" % (name, name))
        out.append("    def __init__(self, *a):")
        if c.base in names:
            out.append("        %s.__init__(self)" % c.base)
        for name, typ, e in c.vars:
            out.append("        self.%s = %s" % (name, tr.expr(e, set()) if e is not None else default_for(typ)))
        if any(f[0] == "_init" for f in c.funcs):
            out.append("        self.__gd_init__(*a)")
        else:
            out.append("        pass")
        for f in c.funcs:
            out.append(tr.func(*f))
        out.append("")
    return "\n".join(out) + "\n"


def main():
    dst = sys.argv[1]
    parsed = [parse_file(p) for p in sys.argv[2:]]
    open(dst, "w").write(translate(parsed))


if __name__ == "__main__":
    main()

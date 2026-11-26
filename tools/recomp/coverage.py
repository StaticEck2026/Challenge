#!/usr/bin/env python3
"""Translator coverage report.

Compares every instruction address the oracle executed (profile_oracle.py
coverage sets, per resident-overlay variant) with the instructions the
translator emitted (build/recomp/manifest.json from recompile.py).

  python3 -I tools/recomp/coverage.py build/recomp/profile_boot.json build/recomp/profile_season.json

Addresses that are deliberately *not* translated but replaced by native host
code are counted separately: the ROM service stubs (F000), the PLINK86 overlay
manager (191F) and its thunks (18B4), and the two switch helpers whose call
sites are translated into direct multi-way branches.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def main():
    args = sys.argv[1:]
    manifest = os.path.join(ROOT, "build", "recomp", "manifest.json")
    if args and args[0].startswith("--manifest="):
        manifest = args.pop(0).split("=", 1)[1]
    man = json.load(open(manifest))
    base = man["load_seg"]
    ovl = {int(k): v for k, v in man["ovl"].items()}
    root_lo, root_hi = man["root"]
    translated = {}
    for f in man["functions"]:
        translated.setdefault(f["unit"], set()).update(f["insns"])
    native = [
        ("ROM service stubs", 0xF0000, 0x100000),
        ("PLINK86 manager", (0x191F + base) << 4, (0x196D + base) << 4),
        ("overlay thunks", ((0x18B4 + base) << 4) + 0xCE, ((0x18B4 + base) << 4) + 0x52A),
        ("switch helper (dense)", ((0x13F3 + base) << 4) + 0xE7, ((0x13F3 + base) << 4) + 0xFE),
        ("switch helper (sparse)", ((0x13F3 + base) << 4) + 0x7DC, ((0x13F3 + base) << 4) + 0x7FB),
    ]
    total = covered = 0
    nat = {n: 0 for n, _, _ in native}
    missing = []
    for path in args:
        d = json.load(open(path))
        for vkey, deltas in d["coverage"].items():
            variant = [int(x) for x in vkey.split(",")] if vkey else []
            a = 0
            for dlt in deltas:
                a += dlt
                hit = None
                for n, lo, hi in native:
                    if lo <= a < hi:
                        hit = n
                        break
                if hit:
                    nat[hit] += 1
                    continue
                total += 1
                if root_lo <= a < root_hi:
                    unit = 0
                else:
                    unit = None
                    for n in variant:
                        if n != 12 and ovl[n][0] <= a < ovl[n][1]:
                            unit = n
                if unit is not None and a in translated.get(unit, ()):
                    covered += 1
                else:
                    missing.append((path, vkey, unit, a))
    print("executed program instructions (all runs, per variant): %d" % total)
    print("covered by translated code: %d (%.3f%%)" % (covered, 100.0 * covered / max(total, 1)))
    for n, c in nat.items():
        print("natively replaced, %-24s %d executed instruction addresses" % (n + ":", c))
    print("NOT covered: %d" % len(missing))
    for p, v, u, a in missing[:40]:
        print("  %s variant [%s] unit %s: %05X" % (os.path.basename(p), v, u, a))
    print("translated: %d functions, %d instruction addresses" % (
        len(man["functions"]), sum(len(s) for s in translated.values())))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())

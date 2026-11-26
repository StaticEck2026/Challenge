#!/usr/bin/env python3
"""Print the roster in an NFL Challenge team file (<TEAM>.NFL).

usage: python3 -I tools/disasm/dump_team.py path/to/89/BEARS.NFL [--check] [--csv]

The format is plain CP437 text with CRLF line ends and a ^Z terminator (see
docs/FILE_FORMATS.md).  Player lines are parsed the way NFL.EXE parses them
(scanf format at DGROUP:0BFC,
 "%d %s %d'%d\" %d %d %d.%d %d %d %d %d %d %s %s %s %s"),
and --check applies the same range checks as the loader in overlay 1
(196D:0683-07D9): jersey 0-100, height 5'0"-7'11", weight 100-400,
years 1-30, speed 4.0-7.9, ratings 50-99.
"""

import argparse
import re
import sys

PLAYER_RE = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(\d+)'\s*(\d+)\"\s+(\d+)\s+(\d+)\s+(\d+)\.(\d+)"
    r"\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*?)\s*$")
FIELDS = ("no", "pos", "ft", "inch", "wt", "yrs", "spd_i", "spd_f",
          "run", "rcv", "blk", "pss", "kck", "name")
RANGES = {"no": (0, 100), "ft": (5, 7), "inch": (0, 11), "wt": (100, 400), "yrs": (1, 30),
          "spd_i": (4, 7), "spd_f": (0, 9), "run": (50, 99), "rcv": (50, 99),
          "blk": (50, 99), "pss": (50, 99), "kck": (50, 99)}


def parse(text):
    text = text.split("\x1a", 1)[0]
    lines = text.replace("\r", "").split("\n")
    team = {"title": lines[0].strip(), "declared": None, "players": [],
            "starters": [], "persona": {}}
    m = re.search(r"Players:\s*(\d+)", lines[1] if len(lines) > 1 else "")
    if m:
        team["declared"] = int(m.group(1))
    section = "players"
    pending = None
    for ln in lines[2:]:
        s = ln.strip()
        if not s or set(s) == {"-"}:
            continue
        if s == "STARTERS":
            section = "starters"
            continue
        if s in ("Offense:", "Defense:"):
            section = "persona"
            pending = s[:-1]
            continue
        if section == "players":
            m = PLAYER_RE.match(ln)
            if m:
                p = dict(zip(FIELDS, m.groups()))
                for k in FIELDS:
                    if k not in ("pos", "name"):
                        p[k] = int(p[k])
                p["name"] = " ".join(p["name"].split()[:4])   # %s %s %s %s
                team["players"].append(p)
        elif section == "starters":
            parts = s.split()
            if len(parts) == 2 and parts[1].isdigit():
                team["starters"].append((parts[0], int(parts[1])))
        elif section == "persona" and pending:
            team["persona"][pending] = s
            pending = None
    return team


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--check", action="store_true", help="apply NFL.EXE's range checks")
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()
    with open(a.file, "rb") as f:
        team = parse(f.read().decode("cp437"))
    out = sys.stdout
    if a.csv:
        out.write(",".join(FIELDS) + "\n")
        for p in team["players"]:
            out.write(",".join(str(p[k]) for k in FIELDS) + "\n")
        return
    out.write(f"{team['title']}\n")
    nfill = sum(p["name"].upper().startswith("FILLER") for p in team["players"])
    out.write(f"players: {len(team['players'])} lines ({nfill} FILLER), "
              f"header says {team['declared']}\n\n")
    out.write(" No Pos  Ht     Wt Yrs Spd  Run Rcv Blk Pss Kck  Name\n")
    for p in team["players"]:
        out.write(f"{p['no']:3d} {p['pos']:4s} {p['ft']}'{p['inch']:2d}\" {p['wt']:4d} {p['yrs']:3d} "
                  f"{p['spd_i']}.{p['spd_f']}  {p['run']:3d} {p['rcv']:3d} {p['blk']:3d} "
                  f"{p['pss']:3d} {p['kck']:3d}  {p['name']}\n")
    by_no = {}
    for p in team["players"]:
        by_no.setdefault(p["no"], p)
    out.write("\nstarters:\n")
    for label, no in team["starters"]:
        p = by_no.get(no)
        out.write(f"  {label:6s} {no:3d}  {p['name'] if p else '?? not on roster'}\n")
    out.write("\npersona: " + ", ".join(f"{k} {v}" for k, v in team["persona"].items()) + "\n")
    if a.check:
        bad = 0
        for p in team["players"]:
            for k, (lo, hi) in RANGES.items():
                if not lo <= p[k] <= hi:
                    bad += 1
                    out.write(f"RANGE: #{p['no']} {p['name']}: {k}={p[k]} not in {lo}..{hi}\n")
        real = [p for p in team["players"] if not p["name"].upper().startswith("FILLER")]
        if team["declared"] is not None and team["declared"] != len(real):
            bad += 1
            out.write(f"COUNT: 'Players: {team['declared']}' != {len(real)} non-filler players\n")
        missing = [lbl for lbl, no in team["starters"] if no not in by_no]
        if missing:
            bad += 1
            out.write("STARTERS not found: " + " ".join(missing) + "\n")
        out.write(f"check: {'OK' if not bad else str(bad) + ' problem(s)'}\n")


if __name__ == "__main__":
    main()

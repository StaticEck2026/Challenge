#!/usr/bin/env python3
"""Reproduce how the Modern view's state addresses (godot/ui/state_map.json)
were found, by memory diffing on the recompiled program (harness.py).

  python3 -I tools/modern/research.py --game-dir /path/to/nfl scoreboard
      runs a game in text mode and lists every DGROUP byte/word whose value
      equals a scoreboard field the game prints (clock, down, play clock,
      scores, time-outs) in >= 97 % of the frames.
  python3 -I tools/modern/research.py --game-dir /path/to/nfl records
      runs a play with instant replay and lists DGROUP word runs that change
      during the play; the 23 x 3-word records at A494 equal the X/O sprite
      positions recognised in CGA memory.
  python3 -I tools/modern/research.py --game-dir /path/to/nfl scroll
      runs a long play and lists the memory words that change exactly when
      the drawn yard lines scroll (cam_y at DGROUP 8755, cam_x at 8753).

The disassembly (tools/disasm) confirms most of them: see the "evidence"
fields of state_map.json and docs/MODERN_UI.md.
"""
import argparse
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402
import scripts  # noqa: E402

D = harness.DGROUP_LINEAR


def parse_scoreboard(text):
    L = text.split("\n")
    if len(L) < 3 or "Timeouts" not in L[0] or "Yard:" not in L[0]:
        return None
    d = {}
    m = re.match(r"\s*(\d+):(\d\d)", L[1])
    if m:
        d["clock_min"], d["clock_sec"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Down:\s+(\d)\b", L[1])
    if m:
        d["down"] = int(m.group(1))
    m = re.search(r"(\d+)\s*$", L[1])
    if m and len(L[1].rstrip()) > 70:
        d["play_clock"] = int(m.group(1))
    for i, row in ((2, L[1]), (1, L[2])):          # row 1 visitor (team 2), row 2 home (team 1)
        t = row[50:54].strip()
        if t.isdigit():
            d["timeouts_%d" % i] = int(t)
        t = row[37:42].strip()
        if t.isdigit():
            d["total_%d" % i] = int(t)
    return d


def cmd_scoreboard(a):
    keys = scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}{wait:20}{Alt-E}"
    r = harness.Run(a.game_dir, "research", "", keys, max_ticks=a.ticks, log=lambda m: None)
    rows = []

    def tick(r):
        if r.mem[0x449] == 3 and r.gm.ticks % 4 == 0:
            d = parse_scoreboard(r.text())
            if d:
                rows.append((d, bytes(r.mem[D:D + 0x10000])))
    r.on_tick = tick
    r.run()
    print("[research] %d scoreboard frames" % len(rows))
    B = np.array([np.frombuffer(dg, dtype=np.uint8) for _, dg in rows]).astype(np.int64)
    W = np.array([np.frombuffer(dg, dtype="<i2") for _, dg in rows]).astype(np.int64)
    W1 = np.array([np.frombuffer(dg[1:] + b"\0", dtype="<i2") for _, dg in rows]).astype(np.int64)
    for key in sorted({k for d, _ in rows for k in d}):
        v = np.array([d.get(key, -99999) for d, _ in rows])
        ok = v != -99999
        if ok.sum() < 5 or len(set(v[ok])) < 2:
            print("  %-12s not enough variety" % key)
            continue
        hits = []
        for M, step, par in ((W, 2, 0), (W1, 2, 1)):
            match = (M[ok] == v[ok][:, None]).mean(axis=0)
            hits += ["%04X" % (i * step + par) for i in np.where(match >= 0.97)[0]]
        print("  %-12s %3d values -> word at DGROUP %s" % (key, len(set(v[ok])), ", ".join(hits[:6]) or "-"))


def cmd_records(a):
    keys = scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}" + "{wait:2000}"
    r = harness.Run(a.game_dir, "research", "/i", keys, max_ticks=1260, log=lambda m: None)
    snaps = {}

    def tick(r):
        if r.mem[0x449] == 4 and r.gm.ticks > 900:     # after the title screen (also mode 4)
            snaps[r.gm.ticks] = bytes(r.mem[D:D + 0x10000])
    r.on_tick = tick
    r.run()
    ts = sorted(snaps)[:80]
    W = np.array([np.frombuffer(snaps[t], dtype="<i2") for t in ts]).astype(np.int64)
    ch = (np.diff(W, axis=0) != 0).sum(axis=0)
    idx = [i for i in np.where(ch >= 3)[0]]
    runs = []
    for i in idx:
        if runs and i - runs[-1][1] <= 4:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    print("[research] DGROUP word runs changing during the first play (%d graphics frames):" % len(ts))
    for lo, hi in runs:
        print("  %04X-%04X  %d words" % (lo * 2, hi * 2 + 1, hi - lo + 1))
    last = snaps[ts[-1]]
    recs = [tuple(int(v) for v in np.frombuffer(last[0xA494 + 6 * i:0xA494 + 6 * i + 6], dtype="<i2"))
            for i in range(23)]
    print("[research] records at DGROUP A494 (x, y, k), last frame: %s" % recs)


def cmd_scroll(a):
    keys = scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}" + "{wait:2000}" * 4
    r = harness.Run(a.game_dir, "research", "/i", keys, max_ticks=a.ticks, log=lambda m: None)
    rows = []

    def tick(r):
        if r.mem[0x449] == 4:
            a8 = np.frombuffer(bytes(r.mem[0xB8000:0xBC000]), dtype=np.uint8)
            px = []
            for y in range(0, 200):
                o = (y & 1) * 0x2000 + (y >> 1) * 80
                row = a8[o:o + 80]
                px.append(int(((row >> 6) == 3).sum() + (((row >> 4) & 3) == 3).sum()
                              + (((row >> 2) & 3) == 3).sum() + ((row & 3) == 3).sum()))
            lines = [y for y, n in enumerate(px) if n > 200]
            rows.append((lines[0] if lines else -1, bytes(r.mem[D:D + 0x10000])))
    r.on_tick = tick
    r.run()
    ly = np.array([x for x, _ in rows])
    chg = np.diff(ly) != 0
    print("[research] %d graphics frames, %d yard-line scrolls" % (len(rows), int(chg.sum())))
    for par in (0, 1):
        M = np.array([np.frombuffer(dg[par:] + (b"\0" if par else b""), dtype="<i2") for _, dg in rows])
        d = np.diff(M.astype(np.int64), axis=0) != 0
        score = (d & chg[:, None]).sum(axis=0) - (d & ~chg[:, None]).sum(axis=0)
        for i in np.argsort(-score)[:3]:
            print("  word at DGROUP %04X: changes on %d of the scrolls, %d other times"
                  % (i * 2 + par, int((d[:, i] & chg).sum()), int((d[:, i] & ~chg).sum())))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("--ticks", type=int, default=6500)
    ap.add_argument("what", choices=["scoreboard", "records", "scroll"])
    a = ap.parse_args()
    {"scoreboard": cmd_scoreboard, "records": cmd_records, "scroll": cmd_scroll}[a.what](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())

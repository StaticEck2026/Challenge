#!/usr/bin/env python3
"""Reproducible end-to-end runs of NFL Challenge in the reference emulator.

Each scenario copies the (read-only) game folder to build/run-<name>/, maps
DOS drives onto that copy, drives the program with a key script and saves
screenshots + text dumps under build/shots/<name>/.

  python3 -I tools/refemu/scenarios.py --game-dir /path/to/nfl boot
  python3 -I tools/refemu/scenarios.py --game-dir /path/to/nfl season
  python3 -I tools/refemu/scenarios.py --game-dir /path/to/nfl all

Drive layout: A: B: C: -> game root, D: -> <copy>/89 (team disk).
"""
import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from run import Session, parse_keys  # noqa: E402

DEFAULT_GAME = os.environ.get("NFL_GAME_DIR", "")


def fresh_copy(game_dir, name):
    dst = os.path.join(ROOT, "build", "run-" + name)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(game_dir, dst, ignore=shutil.ignore_patterns("*.whl"))
    return dst


def session(root, program, args, shots, log):
    drives = {"A": root, "B": root, "C": root, "D": os.path.join(root, "89")}
    return Session(root, program, args, drives, "C", shots=shots, log=log)


def drive(s, script, budget, label, log):
    s.actions = parse_keys(script)
    s.ai = 0
    t0 = time.time()
    n, dt = s.run(budget, progress=30)
    log("[%s] %d emulated / %d executed instructions in %.1fs (%.0f executed instr/s); "
        "ticks=%d exited=%s actions %d/%d" % (label, n, s.m.cpu.executed, dt,
                                              s.m.cpu.executed / max(dt, 1e-6), s.m.ticks,
                                              s.m.exited, s.ai, len(s.actions)))
    return s.ai == len(s.actions)


def scenario_boot(game_dir, log):
    """No config.nfl: configuration dialog -> team selection -> roster load -> game."""
    root = fresh_copy(game_dir, "boot")
    for n in os.listdir(root):
        if n.upper() == "CONFIG.NFL":
            os.remove(os.path.join(root, n))
    shots = os.path.join(ROOT, "build", "shots", "boot")
    s = session(root, "NFL.EXE", "/i", shots, log)
    script = (
        "{when:CONFIGURATION}{shot:01_config_team_disk}4"
        "{when:Color graphics}{shot:02_config_display}4"
        "{when:PC/XT}{shot:03_config_system}1"
        "{when:home team}{shot:04_choose_home}{Enter}"
        "{when:visiting team}{Down}{Enter}"
        "{when:Choose the coach}{shot:05_coach}c"
        "{when:Right keypad}r"
        "{when:coach for the Colts}c"
        "{when:Loading the team roster}{shot:06_loading_roster}"
        "{when:Timeouts}{wait:40}{shot:07_game_start}"
        "{wait:600}{shot:08_game}{wait:600}{shot:09_game}{wait:600}{shot:10_game}"
    )
    ok = drive(s, script, 400_000_000, "boot", log)
    cfg = [n for n in os.listdir(root) if n.upper() == "CONFIG.NFL"]
    log("[boot] config.nfl written: %s" % (open(os.path.join(root, cfg[0]), "rb").read() if cfg else None))
    s.shot("99_final")
    return ok and bool(cfg)


def scenario_replay(game_dir, log):
    """Normal game with /i: wait for the CGA instant replay (graphics mode 4)."""
    root = fresh_copy(game_dir, "replay")
    shots = os.path.join(ROOT, "build", "shots", "replay")
    s = session(root, "NFL.EXE", "/i", shots, log)
    script = ("{when:CONFIGURATION}4{when:Color graphics}4{when:PC/XT}1"
              "{when:home team}{Enter}{when:visiting team}{Down}{Enter}"
              "{when:Choose the coach}c{when:Right keypad}r{when:coach for the Colts}c"
              "{when:Timeouts}")
    drive(s, script, 200_000_000, "replay-setup", log)
    # poll for graphics mode and grab a few frames
    frames = 0
    budget = 0
    while frames < 4 and budget < 1_500_000_000 and not s.m.exited:
        s.run(2_000_000)
        budget += 2_000_000
        if s.m.rb(0x449) in (4, 5, 6):
            s.shot("cga_frame_%d" % frames)
            frames += 1
            s.run(3_000_000)
    log("[replay] captured %d CGA frames" % frames)
    return frames > 0


def scenario_season(game_dir, log):
    """SEASON.COM builds season.nfl, then NFL /s plays it and writes stats.log."""
    root = fresh_copy(game_dir, "season")
    shots = os.path.join(ROOT, "build", "shots", "season")
    for n in os.listdir(os.path.join(root, "89")):
        if n.upper() in ("STATS.LOG", "SEASON.NFL"):
            os.remove(os.path.join(root, "89", n))
    s = session(root, "SEASON.COM", "", shots, log)
    script = ("{when:Drive Letter}{shot:01_season_cfg}D{when:Team Disk is ready}{Space}"
              "{when:Help Screen}{Space}"
              "{when:HOME team}{shot:02_season_home}{Enter}{when:VISITING team}{Down}{Enter}"
              "{wait:20}{shot:03_season_one_game}{Esc}")
    drive(s, script, 100_000_000, "season.com", log)
    sea = [n for n in os.listdir(os.path.join(root, "89")) if n.upper() == "SEASON.NFL"]
    if not sea:
        log("[season] season.nfl was not created")
        return False
    log("[season] season.nfl = %r" % open(os.path.join(root, "89", sea[0]), "rb").read())
    # config.nfl from the distribution copy is used if present; otherwise answer the dialog
    s = session(root, "NFL.EXE", "/s", shots, log)
    script = "{when:Timeouts}{wait:20}{shot:04_season_game}{Alt-E}{wait:10}{shot:05_extra_fast}"
    if not any(n.upper() == "CONFIG.NFL" for n in os.listdir(root)):
        script = "{when:CONFIGURATION}4{when:Color graphics}4{when:PC/XT}1" + script
    drive(s, script, 400_000_000, "nfl /s", log)
    s.shot("06_after_season")
    st = [n for n in os.listdir(os.path.join(root, "89")) if n.upper() == "STATS.LOG"]
    if st:
        src = os.path.join(root, "89", st[0])
        shutil.copy(src, os.path.join(shots, "stats.log"))
        log("[season] stats.log: %d bytes -> %s" % (os.path.getsize(src), os.path.join(shots, "stats.log")))
    log("[season] program exited: %s" % s.m.exited)
    return bool(st)


SCENARIOS = {"boot": scenario_boot, "season": scenario_season, "replay": scenario_replay}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=DEFAULT_GAME, help="folder containing NFL.EXE (read only)")
    ap.add_argument("names", nargs="+", choices=list(SCENARIOS) + ["all"])
    a = ap.parse_args()
    if not a.game_dir or not os.path.exists(os.path.join(a.game_dir, "NFL.EXE")):
        sys.exit("--game-dir must point at the folder with NFL.EXE")
    names = list(SCENARIOS) if "all" in a.names else a.names
    results = {}
    for n in names:
        os.makedirs(os.path.join(ROOT, "build", "shots", n), exist_ok=True)
        logp = os.path.join(ROOT, "build", "shots", n, "run.log")
        with open(logp, "w") as lf:
            def log(msg, lf=lf):
                print(msg, file=sys.stderr)
                lf.write(msg + "\n")
                lf.flush()
            results[n] = SCENARIOS[n](a.game_dir, log)
    for n, ok in results.items():
        print("%-8s %s" % (n, "PASS" if ok else "FAIL"))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

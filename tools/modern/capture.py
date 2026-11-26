#!/usr/bin/env python3
"""Capture real game states for the Modern-UI previews and tests.

Runs the recompiled program headless (harness.py: the generated GDScript and
the host layer translated to Python, deterministic clock) through scripted
games and saves memory snapshots at chosen moments to build/modern/states/:
<name>.bin holds the BIOS data area, the video memory (text page / CGA) and
DGROUP; <name>.json the tick and the screen text.  Nothing here is committed
(build/ is gitignored): the snapshots are derived from the user's game.

  python3 -I tools/modern/capture.py --game-dir /path/to/nfl [scenario ...]

Scenarios: boot (CPU vs CPU with instant replay: setup screens, field,
plays, replay), human (a human coach on the right keypad), full (a whole game
at extra-extra-fast speed: half-time and final statistics, injuries, the
"Another Game" prompt), alt (Dolphins at Bills: team colours that do not
clash).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402
import scripts  # noqa: E402

ROOT = harness.ROOT
OUT = os.path.join(ROOT, "build", "modern", "states")
REGIONS = [(0, 0x500), (0xB8000, 0x4000), (harness.DGROUP_LINEAR, 0x10000)]


def save(r, name, log):
    os.makedirs(OUT, exist_ok=True)
    m = r.mem
    with open(os.path.join(OUT, name + ".bin"), "wb") as f:
        for a, n in REGIONS:
            f.write(bytes(m[a:a + n]))
    meta = {"name": name, "tick": r.gm.ticks, "mode": m[0x449], "text": r.text(), "regions": REGIONS}
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(meta, f, indent=1)
    log("[capture] %-22s tick %6d mode %d" % (name, r.gm.ticks, m[0x449]))


def load(name):
    """1 MB memory image of a saved state (zeros outside the saved regions)."""
    mem = bytearray(0x100000)
    with open(os.path.join(OUT, name + ".bin"), "rb") as f:
        data = f.read()
    pos = 0
    for a, n in REGIONS:
        mem[a:a + n] = data[pos:pos + n]
        pos += n
    with open(os.path.join(OUT, name + ".json")) as f:
        meta = json.load(f)
    return mem, meta


def has(text):
    return lambda r: text in r.text()


def graphics_tick(k):
    """k-th tick of the current graphics-mode stretch."""
    def pred(r):
        st = r.__dict__.setdefault("_gfx", {"n": 0})
        if r.mem[0x449] == 4:
            st["n"] += 1
        else:
            st["n"] = 0
        return st["n"] == k
    return pred


def later(pred, ticks):
    """pred, but only `ticks` after the previous capture."""
    return lambda r: r.gm.ticks - r.__dict__.get("_last_cap", 0) > ticks and pred(r)


def field_with(text):
    def pred(r):
        t = r.text()
        return r.mem[0x449] == 3 and "Timeouts" in t and text in t
    return pred


class Plan:
    """Ordered capture points: (name, predicate, delay_ticks[, keys pressed after])."""

    def __init__(self, points, log):
        self.points = points
        self.i = 0
        self.wait = None
        self.log = log

    def on_tick(self, r):
        # graphics counters must see every tick
        for point in self.points[self.i:self.i + 1]:
            name, pred, delay = point[:3]
            if self.wait is None and pred(r):
                self.wait = delay
            if self.wait is not None:
                if self.wait <= 0:
                    save(r, name, self.log)
                    r._last_cap = r.gm.ticks
                    if len(point) > 3:
                        # keys to press after the capture (run.py key-script syntax)
                        from run import parse_keys
                        for kind, v in parse_keys(point[3]):
                            if kind == "key":
                                r.gm.kbd_queue.extend(v)
                    self.i += 1
                    self.wait = None
                else:
                    self.wait -= 1
        if self.i >= len(self.points):
            r.stop()


def scenario_boot(game_dir, log):
    keys = scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}" + "{wait:2000}" * 6
    pts = [
        ("01_config_drive", has("Team disk in drive A"), 2),
        ("02_config_display", has("Color graphics"), 2),
        ("03_config_system", has("PC/XT"), 2),
        ("04_teams_home", has("Choose the home team"), 3),
        ("05_teams_visitor", has("Choose the visiting team"), 3),
        ("06_matchup_coach", has("Choose the coach for the Bills"), 2),
        ("07_matchup_keypad", has("Right keypad"), 2),
        ("08_coin_toss", has("It is"), 6),
        ("09_loading", has("Loading"), 2),
        ("10_presnap_kickoff", field_with("Yard:"), 30),
        ("11_kickoff_return", field_with("Returned"), 0),
        ("12_play_calling", field_with("Defense selected"), 2),
        ("13_play_snap", graphics_tick(3), 0),
        ("14_play_mid", graphics_tick(28), 0),
        ("15_play_late", graphics_tick(48), 0),
        ("16_replay", graphics_tick(150), 0),
        ("17_play_result", field_with("Tackled by"), 4),
        ("18_play2_snap", graphics_tick(3), 0),
        ("19_play2_mid", graphics_tick(40), 0),
        ("20_play2_replay", graphics_tick(180), 0),
    ]
    r = harness.Run(game_dir, "cap-boot", "/i", keys, max_ticks=9000, log=log)
    plan = Plan(pts, log)
    r.on_tick = plan.on_tick
    r.run()
    return plan.i, len(pts)


def scenario_human(game_dir, log):
    keys = (scripts.CONFIG_KEYS + "{when:home team}{Enter}{when:visiting team}{Down}{Enter}"
            "{when:Choose the coach}h{when:Right keypad}r{when:coach for the Colts}c"
            "{when:Timeouts}{when:Start game}{wait:20}3{wait:300}")
    pts = [
        ("30_human_keypad", has("Right keypad"), 2),
        ("31_human_coach_menu", field_with("Start game"), 10),
        ("32_human_play_menu", later(field_with("group"), 20), 60),
    ]
    r = harness.Run(game_dir, "cap-human", "/i", keys, max_ticks=3000, log=log)
    plan = Plan(pts, log)
    r.on_tick = plan.on_tick
    r.run()
    return plan.i, len(pts)


def scenario_full(game_dir, log):
    keys = scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}{wait:20}{Alt-E}"
    pts = [
        ("40_halftime_stats", has("TEAM STATISTICS"), 4, "{PgDn}"),
        ("40b_stats_rushing", has("- Rushing"), 4, "{PgDn}"),
        ("40c_stats_passing", has("- Passing"), 4, "{PgDn}{PgDn}{PgDn}"),
        ("40d_stats_sacks", has("- Sacks"), 4, "{Alt-S}"),
        ("40e_save_game", has("aved game"), 4),
        ("41_injury", has("SHAKEN"), 2),
        ("42_final_stats", later(has("TEAM STATISTICS"), 40), 4),
        ("43_another_game", has("Another Game"), 2, "n"),
        ("44_new_teams", has("pick new teams"), 2, "n"),
        ("45_exit_menu", has("to exit NFL Challenge"), 2),
    ]
    r = harness.Run(game_dir, "cap-full", "", keys, max_ticks=13500, log=log)
    plan = Plan(pts, log)
    r.on_tick = plan.on_tick
    r.run()
    return plan.i, len(pts)


def scenario_alt(game_dir, log):
    """A second match-up whose colours do not clash: Dolphins at Bills."""
    keys = (scripts.CONFIG_KEYS + "{when:home team}{Enter}{when:visiting team}{Down}{Down}{Enter}"
            "{when:Choose the coach}c{when:Right keypad}r{when:coach for the Dolphins}c"
            "{when:Timeouts}" + "{wait:2000}" * 3)
    pts = [
        ("50_alt_coin_toss", has("It is"), 6),
        ("51_alt_play_calling", field_with("Defense selected"), 2),
        ("52_alt_play_mid", graphics_tick(28), 0),
        ("53_alt_play_result", field_with("Tackled by"), 4),
        ("54_alt_play2_mid", graphics_tick(40), 0),
    ]
    r = harness.Run(game_dir, "cap-alt", "/i", keys, max_ticks=6000, log=log)
    plan = Plan(pts, log)
    r.on_tick = plan.on_tick
    r.run()
    return plan.i, len(pts)


SCENARIOS = {"boot": scenario_boot, "human": scenario_human, "full": scenario_full, "alt": scenario_alt}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("scenario", nargs="*", default=list(SCENARIOS))
    a = ap.parse_args()

    def log(msg):
        print(msg, flush=True)
    ok = True
    for s in a.scenario:
        got, want = SCENARIOS[s](a.game_dir, log)
        log("[capture] %s: %d/%d states" % (s, got, want))
        ok = ok and got == want
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

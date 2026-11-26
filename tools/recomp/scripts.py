"""Scenario definitions shared by the oracle profiler and the lockstep harness.

Each scenario copies the read-only game folder to a writable work directory,
maps DOS drives (A: B: C: -> copy, D: -> copy/89, the team disk) and drives the
program with a key script (syntax: tools/refemu/run.py).
"""
import os
import shutil

# The season file SEASON.COM writes for "one game, Dolphins at Bills" (see
# docs/EMULATION_NOTES.md); written directly so no extra program run is needed.
SEASON_NFL = b"1\r\nBills Dolphins\r\n"

CONFIG_KEYS = "{when:CONFIGURATION}4{when:Color graphics}4{when:PC/XT}1"
TEAM_KEYS = ("{when:home team}{Enter}{when:visiting team}{Down}{Enter}"
             "{when:Choose the coach}c{when:Right keypad}r{when:coach for the Colts}c")

SCENARIOS = {
    # fresh start: CONFIGURATION dialog, team selection, roster load, a game
    # (computer coaches) with instant replay on, played for a long while
    "boot": {"args": "/i", "keys": CONFIG_KEYS + TEAM_KEYS
             + "{when:Loading the team roster}{when:Timeouts}" + "{wait:2000}" * 8,
             "lockstep_keys": CONFIG_KEYS + TEAM_KEYS + "{when:Timeouts}" + "{wait:2000}" * 4,
             "max_ticks": 14000},
    # NFL /s: season mode, extra-extra-fast, plays the game, writes STATS.LOG, exits
    "season": {"args": "/s", "keys": CONFIG_KEYS + "{when:Timeouts}{wait:20}{Alt-E}"},
    # instant replay: same as boot, shorter, until graphics mode is seen
    "replay": {"args": "/i", "keys": CONFIG_KEYS + TEAM_KEYS + "{when:Timeouts}" + "{wait:2000}" * 3},
}

# Scenarios that do not end by themselves stop when their key script is done.
STOP_WHEN_DONE = {"boot", "replay"}


def drives(work):
    return {"A": work, "B": work, "C": work, "D": os.path.join(work, "89")}


def prepare_dir(game_dir, work, scenario):
    """Fresh writable copy of the game folder for one scenario run."""
    if not game_dir or not any(n.upper() == "LEAGUE.NFL" for n in os.listdir(game_dir)):
        raise SystemExit("--game-dir must point at the game folder (with LEAGUE.NFL)")
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(game_dir, work, ignore=shutil.ignore_patterns("*.whl"))
    for n in os.listdir(work):
        if n.upper() == "CONFIG.NFL":
            os.remove(os.path.join(work, n))
    team = os.path.join(work, "89")
    for n in os.listdir(team):
        if n.upper() in ("STATS.LOG", "AVGSTATS.LOG", "SEASON.NFL"):
            os.remove(os.path.join(team, n))
    if scenario == "season":
        with open(os.path.join(team, "SEASON.NFL"), "wb") as f:
            f.write(SEASON_NFL)
    return work

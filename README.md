# NFL Challenge on Godot

This repository runs XOR Corporation's DOS football simulator *NFL Challenge*
(v2.01, 1989) inside **Godot 4**, by **static recompilation**. A translator
that you run once on your own `NFL.EXE` turns every 8086 routine of the program
into a GDScript function ahead of time. Godot then runs those functions on a
native host layer: DOS files on `FileAccess`, the BIOS keyboard and video, the
PLINK86 overlay manager, CGA and HD-text rendering, and the PC speaker. Nothing
interprets 8086 instructions at run time, and nobody re-implemented the game:
the logic is the original program's, translated mechanically (see
`docs/RECOMPILATION.md`).

**No game files are included. Bring your own copy.** It goes in `godot/dos_c/`,
which is gitignored. The recompiled code is derived from your copy and is
written to `godot/recomp/`, which is also gitignored and never committed. See
`godot/README.md`.

## Layout

| Path | What |
|------|------|
| `PROMPT.md`, `PROMPT_RECOMP.md` | The porting prompts that produced this work (emulator, then static recompilation), with errata |
| `tools/recomp/` | The static recompiler (`recompile.py`: loader, decoder, discovery, liveness, GDScript backend), its address-only inputs `profile.json` and `hints.json`, the oracle profiler, the coverage report, the lockstep test and the thread-model smoke test |
| `godot/` | Godot 4.3+ project. `host/` is the native host layer (DOS, BIOS, overlay manager, dispatcher, renderer); `recomp/` (generated, gitignored) is the recompiled program; `main.gd` runs it on a thread and handles display, keyboard, speaker and the game-folder picker; `text_screen.gd` draws text screens with a vector font at native resolution (sharp at 1080p, 16:9) |
| `tools/refemu/` | Python reference emulator, now a **test oracle only**. It runs the real game headless and takes screenshots |
| `tools/disasm/` | Static analysis: MZ and PLINK86 overlay parser, relocation-aware disassembler, xref, team/window dumpers |
| `tools/gdport/` | Test-only GDScript→Python translator and Godot API shim, used to run the Godot code (host layer + recompiled program) in lockstep with the oracle. `legacy_emu/` keeps the retired GDScript interpreter for reference and benchmarks |
| `tools/preview_hd.py` | Renders real game screens at 1920x1080 the way `text_screen.gd` does, using Pillow, so the HD look can be checked without Godot |
| `tools/check_port.py` | Confirms the legacy GDScript interpreter handles every opcode, interrupt service and port the oracle does |
| `docs/` | `RECOMPILATION.md` (design, thread model, hints, limitations), `NFL_EXE_ANALYSIS.md`, `FILE_FORMATS.md`, `EMULATION_NOTES.md`, font license. The bundled TTF fonts and their licenses are in `godot/fonts/` |

Disassembly listings, screenshots and run directories are generated into
`build/`, which is gitignored.

## Quick start

**Godot:** copy the game into `godot/dos_c/` (team disk in a subfolder such as
`89/`) and recompile it once:

```sh
python3 tools/recomp/recompile.py godot/dos_c/NFL.EXE     # writes godot/recomp/ (about 2 s)
```

Then open `godot/` in the Godot 4.3+ editor and press Play. Game flags go
after `--`, for example `godot --path godot -- --args=/i`.

**Python oracle (headless):**

```sh
G=/path/to/your/nfl          # folder containing NFL.EXE
python3 -I tools/recomp/lockstep.py --game-dir $G season    # recompiled GDScript vs oracle, /s game
python3 -I tools/refemu/test_cpu.py                          # 87 CPU tests
python3 -I tools/refemu/scenarios.py --game-dir $G season    # SEASON.COM + full CPU-vs-CPU game -> stats.log
python3 -I tools/refemu/scenarios.py --game-dir $G boot replay
python3 -I tools/disasm/mz_info.py $G/NFL.EXE                # header + overlay table
python3 -I tools/disasm/disasm.py  $G/NFL.EXE                # listings -> build/disasm/
```

## Status

- **Verified by running the Python oracle:** configuration writes
  `config.nfl`, team selection and roster loading work, a full
  computer-vs-computer `/s` season game writes `stats.log`, and instant-replay
  frames are drawn in CGA mode 4.
- **Verified for the recompiled program** (the generated GDScript plus the
  GDScript host layer, translated to Python by `tools/gdport/gd2py.py` and run
  in lockstep with the oracle under a shared deterministic clock; see
  `docs/EMULATION_NOTES.md`):
  - 100% of the instructions the oracle executed are covered by translated
    code, with zero dispatch misses.
  - A complete `/s` season game matches the oracle at all 728,068 service
    calls and 364 memory checkpoints, and writes a **byte-identical
    `STATS.LOG`**. It runs 5x faster than the interpreter (49 s against 246 s).
  - The boot scenario matches as well: the configuration dialog, team
    selection, roster load and 8,000 ticks of a game with instant replay, at
    541,803 service calls. The CGA video memory is identical at every one of
    the 4,137 ticks spent in graphics mode.
  - Every `.gd` file passes `gdparse`, and `gdlint` with a relaxed config for
    the generated scripts. `godot/` contains no instruction interpreter.
- **Not verified:** the project has **never been opened in a real Godot
  editor**, because downloading the Godot binary was blocked in the build
  sandbox. The following are untested in Godot itself: compiling about
  490,000 generated lines, the program `Thread`, `main.gd` (window, input,
  texture upload, audio, file dialog), `text_screen.gd`, and GDScript speed.
  The thread model was exercised with Python threads
  (`tools/recomp/thread_smoke.py`). Expect small fixes on the first run.

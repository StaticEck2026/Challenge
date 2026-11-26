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

By default the game is shown in the **Modern view**: a presentation layer
that reads the running program's memory and redraws every screen with flat
SVG vector art in a 2D "match centre" style (the field top-down with the
players as numbered team-coloured discs, a scoreboard pill, cards and
buttons). It only reads memory and only sends the keys a screen asks for; the
original program still decides everything. The HD-text and Classic (original
bitmap) views are one click away. See `docs/MODERN_UI.md`.

**No game files are included. Bring your own copy.** It goes in `godot/dos_c/`,
which is gitignored. The recompiled code is derived from your copy and is
written to `godot/recomp/`, which is also gitignored and never committed.
`NFL.EXE` is needed only once, as the recompiler's input. The running game uses
only the recompiled code, the baked program image and the data files. See
`godot/README.md`.

## Layout

| Path | What |
|------|------|
| `PROMPT.md`, `PROMPT_RECOMP.md` | The porting prompts that produced this work (emulator, then static recompilation), with errata |
| `tools/recomp/` | The static recompiler (`recompile.py`: loader, decoder, discovery, liveness, GDScript backend), its address-only inputs `profile.json` and `hints.json`, the oracle profiler, the coverage report, the lockstep test and the thread-model smoke test |
| `godot/` | Godot 4.3+ project. `host/` is the native host layer (DOS, BIOS, overlay manager, dispatcher, renderer); `recomp/` (generated, gitignored) is the recompiled program; `main.gd` runs it on a thread and handles display, keyboard, speaker and the game-folder picker; `ui/` is the Modern view (game-state reader with its address map `state_map.json`, screen classifier and layouts, SVG art in `ui/art/`); `text_screen.gd` draws text screens with a vector font at native resolution (sharp at 1080p, 16:9) |
| `tools/modern/` | Modern-view tools: headless runs of the recompiled program (`harness.py`), state capture, SVG/PNG previews of the shipped layout code (`render.py`, `rasterize.js`), the player-position check against the CGA frame, and the memory-diffing research that found the addresses |
| `tools/refemu/` | Python reference emulator, now a **test oracle only**. It runs the real game headless and takes screenshots |
| `tools/disasm/` | Static analysis: MZ and PLINK86 overlay parser, relocation-aware disassembler, xref, team/window dumpers |
| `tools/gdport/` | Test-only GDScript→Python translator and Godot API shim, used to run the Godot code (host layer + recompiled program) in lockstep with the oracle. `legacy_emu/` keeps the retired GDScript interpreter for reference and benchmarks |
| `tools/preview_hd.py` | Renders real game screens at 1920x1080 the way `text_screen.gd` does, using Pillow, so the HD look can be checked without Godot |
| `tools/check_port.py` | Confirms the legacy GDScript interpreter handles every opcode, interrupt service and port the oracle does |
| `docs/` | `RECOMPILATION.md` (design, thread model, hints, limitations), `MODERN_UI.md` (the Modern view: design, game-state address map and evidence, screens, previews), `NFL_EXE_ANALYSIS.md`, `FILE_FORMATS.md`, `EMULATION_NOTES.md`, font license. The bundled fonts and their licenses are in `godot/fonts/` |

Disassembly listings, screenshots and run directories are generated into
`build/`, which is gitignored.

## Quick start

**Godot:** recompile your game folder once (both `NFL.EXE` and `SEASON.COM`):

```sh
python3 tools/recomp/recompile.py /path/to/nfl --bundle-data   # writes godot/recomp/ (about 2 s)
tools/godot_check.sh                                           # optional: official Godot 4.3, headless checks
```

This writes the generated scripts, `program.bin` (the baked program image),
`season/` (SEASON.COM, run with `-- --program=season`) and `game/` (a copy of
the data files). After that the game needs no `NFL.EXE` or `SEASON.COM`.
`tools/godot_check.sh` downloads and verifies the official Godot 4.3 binary,
imports the project, compiles every script and runs the headless smoke tests.
Notes for Windows and macOS are in `godot/README.md`, under "Check it in
Godot".
Without `--bundle-data`, put the data files (`A`..`Q`, `LEAGUE.NFL`, `89/`)
into `godot/dos_c/` or pick their folder in the app. Then open `godot/` in the
Godot 4.3+ editor and press Play. Game flags go
after `--`, for example `godot --path godot -- --args=/i`.

**Python oracle (headless):**

```sh
G=/path/to/your/nfl          # folder containing NFL.EXE
python3 -I tools/recomp/lockstep.py --game-dir $G seasoncom # recompiled SEASON.COM vs oracle -> season.nfl
python3 -I tools/recomp/lockstep.py --game-dir $G season    # recompiled GDScript vs oracle, /s game
python3 -I tools/recomp/smoke_test.py                        # main.gd's --smoke mode, through gd2py
python3 -I tools/refemu/test_cpu.py                          # 87 CPU tests
python3 -I tools/refemu/scenarios.py --game-dir $G season    # SEASON.COM + full CPU-vs-CPU game -> stats.log
python3 -I tools/refemu/scenarios.py --game-dir $G boot replay
python3 -I tools/disasm/mz_info.py $G/NFL.EXE                # header + overlay table
python3 -I tools/disasm/disasm.py  $G/NFL.EXE                # listings -> build/disasm/
python3 -I tools/modern/capture.py --game-dir $G             # Modern view: real game states
python3 -I tools/modern/render.py                            # -> build/shots/modern/*.png (1920x1080)
python3 -I tools/modern/check_positions.py --game-dir $G --fast
python3 -I tools/recomp/lockstep.py --game-dir $G --with-ui season   # lockstep with the Modern layer active
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
    code, for both NFL.EXE and SEASON.COM, with zero dispatch misses.
  - Recompiled **SEASON.COM** (Turbo Pascal 3.0) matches the oracle at all
    8,380 service calls and on 5 screens, and writes a byte-identical
    `season.nfl`. A complete `/s` game on that schedule matches at all 76,782
    service calls, 38 memory checkpoints and 1,265 graphics-mode ticks, and
    writes a **byte-identical `STATS.LOG`**, 6.8x faster than the interpreter.
    The test clock counts loop back-edges as polls, so these numbers differ
    from earlier runs.
  - The boot scenario matches as well: the configuration dialog, team
    selection, roster load and 8,000 ticks of a game with instant replay, at
    16,496 service calls. The CGA video memory is identical at every one of
    the 4,904 ticks in graphics mode.
  - These runs delete `NFL.EXE` and set the program up from the baked
    `program.bin`.
  - Every `.gd` file passes `gdparse`, and `gdlint` with a relaxed config for
    the generated scripts. `godot/` contains no instruction interpreter.
- **Modern view** (`docs/MODERN_UI.md`): the shipped layout code, translated
  to Python, renders real game states captured from the recompiled program
  to SVG, rasterised to 1920x1080 PNGs with Chromium. A check compares every
  player position the view draws with the X/O sprites the game drew in the
  CGA frame, and the yard scale with the yard lines it drew. `lockstep.py
  --with-ui` passes for `season` and `boot` with the layer running on every
  tick, so it is passive.
- **Not verified:** the project has **never been opened in a real Godot
  editor**, because downloading the Godot binary was blocked in the build
  sandbox. The following are untested in Godot itself: compiling about
  490,000 generated lines, the program `Thread`, `main.gd` (window, input,
  texture upload, audio, file dialog), `text_screen.gd`, the Modern view's
  Godot drawing (`ui/modern_view.gd`: SVG rasterisation, text, input), and
  GDScript speed.
  The thread model and the `--smoke` self-test were exercised with Python
  threads (`tools/recomp/thread_smoke.py`, `tools/recomp/smoke_test.py`).
  `tools/godot_check.sh` runs these checks with the real engine on your
  machine.

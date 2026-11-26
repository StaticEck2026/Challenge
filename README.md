# NFL Challenge on Godot

This repository runs XOR Corporation's DOS football simulator *NFL Challenge*
(v2.01, 1989) inside **Godot 4**. It does this by **emulating the PC**: an 8086
CPU, plus the BIOS, DOS and CGA services the game uses, all written in GDScript.
It does not re-implement the game. The original, unmodified `NFL.EXE` runs
exactly as it did on DOS.

**No game files are included. Bring your own copy.** It goes in `godot/dos_c/`,
which is gitignored. See `godot/README.md`.

## Layout

| Path | What |
|------|------|
| `PROMPT.md` | The porting prompt that produced this work, with errata found while executing it |
| `godot/` | Godot 4.3+ project. `emu/` is the PC emulator in GDScript; `main.gd` handles display, keyboard, speaker and the game-folder picker; `text_screen.gd` draws text screens with a vector font at native resolution (sharp at 1080p, 16:9) |
| `tools/refemu/` | Python reference emulator (the oracle) with the same structure as the GDScript one. It runs the real game headless and takes screenshots |
| `tools/disasm/` | Static analysis: MZ and PLINK86 overlay parser, relocation-aware disassembler, xref, team/window dumpers |
| `tools/gdport/` | Test-only GDScript→Python translator, used to run the Godot emulator code in lockstep with the oracle |
| `tools/preview_hd.py` | Renders real game screens at 1920x1080 the way `text_screen.gd` does, using Pillow, so the HD look can be checked without Godot |
| `tools/check_port.py` | Confirms the GDScript port handles every opcode, interrupt service and port the oracle does |
| `docs/` | `NFL_EXE_ANALYSIS.md`, `FILE_FORMATS.md`, `EMULATION_NOTES.md`, font license. The bundled TTF fonts and their licenses are in `godot/fonts/` |

Disassembly listings, screenshots and run directories are generated into
`build/`, which is gitignored.

## Quick start

**Godot:** copy the game into `godot/dos_c/` (team disk in a subfolder such as
`89/`), open `godot/` in the Godot 4.3+ editor, and press Play. Game flags go
after `--`, for example `godot --path godot -- /i`.

**Python oracle (headless):**

```sh
G=/path/to/your/nfl          # folder containing NFL.EXE
python3 -I tools/refemu/test_cpu.py                          # 87 CPU tests
python3 -I tools/refemu/scenarios.py --game-dir $G season    # SEASON.COM + full CPU-vs-CPU game -> stats.log
python3 -I tools/refemu/scenarios.py --game-dir $G boot replay
python3 -I tools/disasm/mz_info.py $G/NFL.EXE                # header + overlay table
python3 -I tools/disasm/disasm.py  $G/NFL.EXE                # listings -> build/disasm/
```

## Status

- **Verified by running the Python oracle:**
  - Configuration writes `config.nfl`.
  - Team selection and roster loading work.
  - A full computer-vs-computer game in `/s` season mode completes in about 90
    seconds and writes `stats.log`.
  - Instant-replay frames in CGA mode 4 are drawn.
- **Verified for the GDScript emulator core (`godot/emu/`):** translated to
  Python, it stayed identical to the oracle (registers, memory, screen) through a
  complete season game, 110M instructions, and produced a byte-identical
  `stats.log`. Every `.gd` file passes `gdparse` and `gdlint`.
- **Not verified:** the project has **never been opened in a real Godot editor**.
  Downloading the Godot binary was blocked in the build sandbox. `main.gd`
  (window, input, texture upload, audio, file dialog) and the HD text
  renderer `text_screen.gd` are untested in Godot itself, and so is GDScript
  speed. The HD layout was checked through its Pillow mirror,
  `tools/preview_hd.py`. Expect small fixes on the first run.

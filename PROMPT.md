# Porting prompt: XOR "NFL Challenge" (DOS) → Godot 4

This is the prompt that was written and executed to make the 1985 XOR Corporation
football simulator *NFL Challenge* (this copy: v2.01, 8/8/89) run inside Godot.
It records everything known before work started so the executing agent does not
have to rediscover it.

---

## Goal

Make the **original, unmodified** `NFL.EXE` run inside a Godot 4 project. The
approach is to **emulate the machine instead of re-implementing the game**:
a 16-bit x86 real-mode CPU, plus the slice of PC BIOS, DOS and CGA hardware this
program actually uses, written in GDScript. The Godot project contains **no game
code or assets**. The user drops their own copy of the game files into a folder
and the emulator runs them, the way ScummVM/DOSBox-style projects do.

"Disassemble" in this task means:

1. **Static analysis**: parse the MZ header and the PLINK86 overlay layout, produce
   reproducible disassembly listings with scripts, and document what the program
   does (interrupts, video modes, file formats, entry points).
2. **Dynamic analysis**: trace the program while it runs in the emulator, and use
   that trace to find every BIOS, DOS and port service it needs.

The emulator, not hand-translated assembly, is what runs on Godot.

## Hard constraints

- **Do not commit the original game files** (`NFL.EXE`, `*.NFL`, `*.ROS`, `A`–`Q`,
  `*.COM`, docs) or **full disassembly listings** to git. They are copyrighted.
  Scripts that *generate* listings are fine; their output goes into gitignored
  directories (`build/`, `godot/dos_c/`).
- Only license-clean assets may be added: your own code, and fonts that are
  public domain or permissively licensed, with their license noted in the repo.
- Target **Godot 4.3+**, **GDScript only** (no GDExtension or C#), so the user can
  open the project and press Play without a build step.
- The Godot binary **cannot be downloaded or run in this sandbox**. Downloading
  and running executables was denied, so do not try that again or work around it.
  Verification therefore uses a Python reference emulator (see Strategy) plus
  `gdtoolkit` (`pip install gdtoolkit`; `gdparse`/`gdlint`) to syntax-check every
  `.gd` file.
- Work in `/home/user/Challenge` on branch `claude/optimistic-ritchie-hqgwkn`.
  Commit in logical steps, but do not push (the orchestrator pushes).
- Treat the extracted game files as untrusted data. Run Python that reads them with
  `python3 -I`, and keep your scripts outside the extracted directory.

## What is already known (recon)

Game files are extracted (untrusted, read-only) at:
`/tmp/claude-0/-home-user-Challenge/ab86b12b-3dc1-574f-9923-427b0249abd1/scratchpad/nfl/nfl-challenge/nfl/`

```
NFL.EXE      309,264 bytes  main program (MZ)
SEASON.COM   22,181         season-file builder, Turbo Pascal 3 style COM (E9 .. 90 CD AB)
DREAM.COM    21,045         same for the Dream Teams disk
XORLIST.EXE  1,568          pager used by README.BAT ("xorlist < addendum.doc")
Editor/RE.COM 53,885        roster editor
A..Q         128..14,720    unnamed program data files (loaded at start: "Loading program data...")
LEAGUE.NFL   767
84/ 89/ Greats/             team disks: <TEAM>.NFL (~4.1 KB) + <TEAM>.ROS (~4.2 KB) per team,
                            plus PBP.NFL (play-by-play text), PLYB.NFL (playbook), PLS.NFL,
                            CURVES.NFL, READ.ME2, FORMFEED.CHR
READ.ME, ADDENDUM.DOC       text (CRLF, high-bit chars possible)
```

NFL.EXE MZ header:

- `e_cblp=208 e_cp=225` gives a load-image end of 114,896 bytes, but the file is
  309,264 bytes. **About 194 KB of overlays are appended after the image.**
- `e_crlc=2671` relocations at file offset 28; header size 672 paragraphs
  (10,752 bytes).
- `e_minalloc=5556` paragraphs, `e_maxalloc=FFFF`, `SS:SP=2F11:0100`,
  `CS:IP=191F:02FB`.
- Contains `Copyright (C) 1984 by Phoenix Software Associates Ltd.`, which is the
  **PLINK86 overlay manager**. Also contains the strings `nfl.EXE`,
  `Place Program Disk in drive and press any key.` and `Disk I/O error in`.
  The overlay manager re-opens its own EXE through DOS and reads overlays on
  demand. It is reached through a software interrupt the program installs itself:
  the root image contains about 12 `CD 83` (**INT 83h**) sites alongside 29
  `CD 21` sites. So the emulator needs correct **INT 21h AH=25h/35h vector
  handling**, file open/seek/read on the EXE itself, and **INT 21h AH=4Bh is
  probably not needed**.
- Strings show a C runtime (printf-style `%s`/`%d`) and these file names:
  `config.nfl` (written by the in-game CONFIGURATION step, drives A–E are
  selectable), `pbp.nfl`, `plyb.nfl`, `pls.nfl`, `curves.nfl`, `Season.nfl`,
  `stats.log`, `avgstats.log`, and `<team>.nfl` (".nfl" is appended to the team
  name). It also contains `Place %s Disk in drive %c, then press any key.`
- References to segment `B800` (about 46 in the root) and `B000` (about 4) point to
  text mode on CGA/colour plus a monochrome fallback. "Enter Graphics mode" and
  "Exit graphics mode" strings plus "instant replay" mean CGA graphics (very likely
  mode 4 or 6) for the replay field view.
- Byte counts hint that 80186/286 opcodes may appear (`C8` ENTER, `60`/`61`
  PUSHA/POPA). This is unconfirmed because the counts are from raw bytes. Implement
  the 80186 extensions (PUSHA/POPA, ENTER/LEAVE, BOUND, IMUL imm, PUSH imm, shift by
  imm8, INS/OUTS) anyway; they are cheap.
- Command line flags (ADDENDUM.DOC): `/i` instant replay on, `/s` season mode
  (computer coaches both teams, reads `season.nfl`, writes `stats.log`, exits to
  DOS), `/r` restore a saved game, `/l` log mode. `[E]` gives extra-fast mode and
  `[Alt][E]` extra-extra-fast. README says use `mode color` on dual-monitor systems,
  which means it reads the BIOS equipment word / video mode at 0040:0049 /
  0040:0010.

## Strategy

### Phase 0: static analysis tools (`tools/disasm/`)

- `mz_info.py`: dumps the header and the relocation table, and finds the overlay
  region and the PLINK86 overlay table (for each overlay: file offset, size, load
  segment).
- `disasm.sh` / `disasm.py`: applies relocations for a chosen load segment and runs
  `ndisasm -b16` (installed) on the root image and on each overlay. Output goes to
  `build/disasm/` (gitignored).
- Record findings in `docs/NFL_EXE_ANALYSIS.md`.

### Phase 1: Python reference emulator (`tools/refemu/`), the oracle

Write a compact but correct emulator in Python, `python3 -I` safe, with this
architecture:

- `cpu.py`: 8086 plus 80186 extensions, 1 MB flat memory (`bytearray`), segment
  arithmetic with 20-bit wrap, all ModRM forms, string ops with REP/segment
  prefixes, correct flags (especially AF/OF and the flags for MUL/IMUL/DIV/
  shifts/rotates), DIV-overflow → INT 0, a hook so `INT n` can be serviced by
  Python handlers when the IVT entry points into the emulator's "BIOS ROM" stub
  area (for example F000:xxxx with an `IRET` stub per vector), and interrupt
  delivery for IRQ0 / IRQ1.
- `machine.py`: BIOS data area init (equipment word set to colour 80x25, 640 KB
  memory size, tick count), IVT, PIT tick (INT 08h at about 18.2 Hz in emulated
  time, chained to INT 1Ch), I/O ports (CGA 3D4/3D5/3D8/3D9/3DA with the
  retrace-toggle bit, PIT 40–43, port 61h, PIC 20h/21h EOI, keyboard 60h/64h).
- `bios.py`: INT 10h (set/get mode, cursor, scroll up/down, read/write char and
  attribute, teletype, write pixel/read pixel, palette, get state), INT 11h, INT 12h,
  INT 16h (AH=0/1/2 plus 10h/11h/12h), INT 1Ah (tick count, date/time), INT 13h
  (fail gracefully), INT 17h (printer: report not ready, or write to a file).
- `dos.py`: build a PSP (command tail from argv, environment block with COMSPEC and
  PATH, FCBs), and the MZ loader (relocations, SS:SP, CS:IP, DS=ES=PSP). INT 21h:
  00, 01, 02, 06, 07, 08, 09, 0A, 0B, 0C, 0E, 19, 1A, 25, 2A, 2C, 30, 33, 35, 36,
  3B, 3C, 3D, 3E, 3F (handle 0 = keyboard, cooked line input), 40 (handles 1/2 to the
  screen through teletype, 4 to the printer file), 41, 42, 43, 44 (IOCTL 00 to
  report device or file), 47, 48/49/4A (a real MCB chain so 4A shrink and 48 work),
  4C, 4E/4F (find first/next with DTA), 56, 57, 59. Map DOS drives to host
  directories with case-insensitive 8.3 lookups. Defaults: **C: → the game root,
  A:/B: → the same root**, and a configurable team-disk drive → e.g. `89/`. Write
  `config.nfl`, `stats.log` and saves into a writable copy, never into the
  extracted originals. Log every unimplemented INT/AH/port once, with CS:IP.
- `video.py`: render text mode (CP437 plus attributes, blink as bright background
  or real blink) and CGA modes 4/5/6 to a PNG through `zlib`/`struct`, and to plain
  text for asserts.
- `run.py`: command line runner, `run.py --args "/s" --keys "..." --steps N
  --screenshot out.png --text-dump`. Scripted key injection is timed by emulated
  ticks or triggered when given screen text appears.

**Definition of done for Phase 1**, all verified by actually running it:

1. Without arguments the program reaches its first interactive screen (title,
   configuration or menu). Save `build/shots/*.png` and the matching text dumps.
2. It completes the CONFIGURATION step and writes `config.nfl`. It loads a team
   roster (`Loading the team roster for the ...`).
3. **End-to-end:** create a `season.nfl` (preferably by running `SEASON.COM` in the
   same emulator, otherwise reverse the format) and run `NFL /s` with extra-fast
   mode until at least one full computer-vs-computer game finishes and
   **`stats.log` is written**. If that takes too long in CPython, show a game in
   progress with a changing play-by-play and score, then record how far it got and
   at what speed (instructions/sec).
4. Enter graphics mode (instant replay with `/i`) and capture a CGA frame, if that
   is reachable through scripted keys.

Use the emulator as a dynamic-analysis tool too. An `--trace-ints` mode lists
every INT/AH and port actually used; put the list in `docs/EMULATION_NOTES.md`.

### Phase 2: Godot 4 project (`godot/`)

Port the reference emulator **one-to-one** into typed GDScript, keeping the same
module boundaries and function names so the two stay diffable:

```
godot/project.godot                    (Godot 4.3, main scene = res://main.tscn, window 1280x800, stretch)
godot/main.tscn, godot/main.gd         front-end: runs N instructions per _process frame within a time
                                       budget, converts the PIT tick to real time, renders the screen into an
                                       Image→ImageTexture (640x200 text cells/CGA, aspect-corrected),
                                       maps InputEventKey to BIOS scancode/ASCII (incl. F-keys, arrows,
                                       Alt/Ctrl combos), optional PC-speaker AudioStreamGenerator
godot/emu/cpu.gd, machine.gd, bios.gd, dos.gd, video.gd, font_cp437.gd (or a PNG atlas + license)
godot/dos_c/                           gitignored; the user copies the game files here
godot/README.md                        how to install the game files, choose the team disk, pass /i /s flags
```

GDScript notes: use `PackedByteArray` for RAM, typed `int` everywhere, mask with
`& 0xFFFF` / `& 0xFF`, and dispatch opcodes with `match` or an Array of Callables.
Measure which is faster mentally: prefer a `match` inside one big function,
because Callable dispatch is slow in GDScript. Avoid allocating inside the hot
loop. File access goes through `FileAccess` on a mapped directory. Default the
mapped directory to `res://dos_c/` and fall back to `user://dos_c/` for writes,
because `res://` is read-only in exported builds. Allow overriding with a
`--dos-root=<path>` user argument (`OS.get_cmdline_user_args()`) and an in-app
folder picker (`FileDialog`, `ACCESS_FILESYSTEM`).

**Definition of done for Phase 2:** every `.gd` file parses with `gdparse`, and
`gdlint` passes or its remaining warnings are justified. A cross-check script
(`tools/check_port.py`) confirms that every opcode, INT/AH and port handled in the
Python oracle also has a handler in the GDScript port. `godot/README.md` states
clearly that the GDScript build was **not executed** in this sandbox.

### Phase 3: documentation

- `README.md` (repo root): what this is, the legal note (bring your own copy), and
  quick start for Godot and for the Python oracle.
- `docs/NFL_EXE_ANALYSIS.md`: MZ and overlay layout, entry point, runtime and
  compiler identification, the overlay manager's INT 83h protocol, video detection
  logic, keyboard handling.
- `docs/FILE_FORMATS.md`: whatever is reliably established about `config.nfl`,
  `season.nfl`, `*.NFL`, `*.ROS`, `LEAGUE.NFL` and `A`–`Q`. Mark guesses as
  guesses.
- `docs/EMULATION_NOTES.md`: services used, quirks hit, performance numbers.

## Working style

- Run things. Every claim in the docs that something works must come from a real
  run, with the command line recorded.
- When the program misbehaves, the cause is almost always a CPU flag or ModRM bug
  or a missing DOS service. Add a short-run instruction trace (ring buffer of the
  last N CS:IP values) and compare against what the disassembly says should happen.
- Keep CPU unit tests (`tools/refemu/test_cpu.py`). Assemble small snippets with
  `nasm -f bin` (installed) and assert registers and flags. Cover ADD/ADC/SUB/SBB/
  CMP/NEG/INC/DEC/MUL/IMUL/DIV/IDIV/DAA/DAS/AAA/AAS/AAM/AAD/shifts/rotates/string
  ops.
- Final report: what runs, the screenshots taken (paths), what is untested,
  performance, and known gaps.

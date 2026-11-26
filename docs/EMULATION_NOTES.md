# Emulation notes

How `NFL.EXE` is run: by the Python reference emulator (`tools/refemu/`, the
"oracle") and by its one-to-one GDScript port (`godot/emu/`). This file lists
the services the program really uses (measured with `--trace-ints`), the quirks
we hit, the performance, and the commands that reproduce every claim. Static
analysis of the binary is in `docs/NFL_EXE_ANALYSIS.md`.

All runs use a writable copy of the game folder under `build/` (gitignored).
Nothing from the game is committed.

## Architecture (both ports)

| Module | Python | GDScript | Role |
|--------|--------|----------|------|
| CPU | `cpu.py` | `cpu.gd` | 8086 + 80186 (PUSHA/POPA, ENTER/LEAVE, BOUND, IMUL imm, PUSH imm, shift imm, INS/OUTS); eager flags; DIV overflow → INT 0; ESC (D8–DF) decoded and ignored; `F1 nn` = emulator trap |
| Machine | `machine.py` | `machine.gd` | 1 MB memory, ROM stubs `F1 nn CF` at F000:E000+4n for every vector, timer stub `F1 08 / INT 1C / EOI / IRET`, 8259 PIC, 8253 PIT, CGA ports, keyboard controller, speaker; main loop with idle fast-forward |
| BIOS | `bios.py` | `bios.gd` | INT 08 09 10 11 12 13 14 15 16 17 18/19 1A 1B 1C on the real BIOS data area |
| DOS | `dos.py` | `dos.gd` | MZ/COM loader, PSP/env/MCB, INT 20 21 24 27 28 29 2F 33 67, drive mapping |
| Video | `video.py` | `video.gd` | text (CP437, attributes, blink) and CGA 4/5/6 → PNG / Image, text dump |
| Runner | `run.py`, `scenarios.py` | `main.gd` | key scripts, screenshots / Godot front-end |

Interrupt services are reached the way the game reaches them. NFL.EXE has no
literal `INT 10h`/`INT 16h`: its C runtime `int86()` (13F3:0717) pushes
FLAGS/CS/IP and jumps through the IVT. The IVT therefore points at real code
(the ROM stubs), and the trap runs the service when execution arrives there.
Services return flags by patching the FLAGS word that the stub's `IRET` pops
(CF, ZF, SF, OF, PF and AF), which is how `kbhit()` sees ZF from INT 16h AH=01.

A service that must wait for input (INT 16h AH=00, INT 21h AH=01/07/08/0A,
handle-0 reads) returns "block": IP is moved back onto the trap, IF is set as
the real BIOS does, and the CPU idles until the next timer or keyboard IRQ.

## Services actually used by NFL.EXE (dynamic trace)

Measured with `run.py --trace-ints` over two runs: (1) a fresh start without
`config.nfl` through the configuration dialog, team selection, roster load and
a computer-vs-computer game with `/i`; (2) `NFL /s` with a `season.nfl`,
extra-extra-fast mode, to the end of the game, `stats.log` and exit.

| Vector | AH values seen | Notes |
|--------|----------------|-------|
| INT 10h | 00 01 02 03 05 06 07 09 0B 0F | mode set (3 text, 4 CGA replay), cursor shape/position, page, scroll, write char+attr, palette, get mode. Called via `int86()` |
| INT 16h | 00 01 02 | read key, key available (ZF), shift flags. Called via `int86()`. The game also flushes the keyboard by writing 0040:001A/001C directly, so INT 16h must use the real BDA ring buffer |
| INT 1Ah | 00 | tick count (random seed) |
| INT 21h | 2C 30 3C 3D 3E 3F 40 42 44 4A 4C EF | time (polled constantly in delay loops), version, create/open/close/read/write/seek, IOCTL AL=0 (device or file), shrink memory, exit; AH=EF is a network probe (left unanswered, BX unchanged) |
| INT 08h / 09h / 1Ch | – | timer and keyboard IRQ handlers are the emulator's own (the game does not hook them) |

Static analysis also finds INT 21h AH=02, 07, 09, 1A, 41, 45 and the DOS 1.x
FCB path AH=0F/27/29 (only taken under DOS 1.x; we report 3.30). All of them are
implemented and covered by `tools/refemu/test_dos.py`.

I/O ports touched by the game: `3DAh` in (CGA status: it waits for bit 3,
vertical retrace, before every text-screen copy), `3D8h` out (25h video off,
29h 80x25 on), `61h` in/out, `43h` and `42h` out (PC speaker tone). Ports
`20h` and `60h` in the trace come from the emulator's own INT 08/09 ROM code.
No PIC, keyboard or CRTC programming by the game.

Files opened (in order) during `/s`: `nfl.EXE` (by the PLINK86 overlay
manager, relative name, then each PATH entry), `config.nfl`,
`d:Season.nfl`, `league.nfl`, program data `M O P Q a K L g h i B C D E F`
(mixed case!), `d:pbp.nfl`, `d:plyb.nfl`, `d:pls.nfl`, `d:curves.nfl`,
`d:Bills.nfl`, `d:Dolphins.nfl`, `n`, `J`, then `d:stats.log` (opened for
append, created if missing). Hence case-insensitive name lookup is mandatory.

Unimplemented things reported at run time (logged once each): only
`INT 21h AH=EFh` (deliberately unanswered) and the `FNINIT` (DB E3) 8087
probe at 1515:0019 (ignored, so the runtime sees no coprocessor).

## Quirks hit

* **CGA status port.** The text-screen copy routine spins on `3DAh` bit 3.
  The port toggles its value on every read (`00h`, `00h`, `09h`, `09h`), so
  both "wait for retrace" and "wait for end of retrace" loops end quickly.
* **Busy-wait delays.** The game times its messages by polling INT 21h AH=2Ch
  and INT 16h AH=01 in a loop. A heuristic in INT 16h AH=01 (more than 8 empty
  polls from the same return address less than 2000 instructions apart)
  fast-forwards emulated time to the next timer tick. About 40–60% of emulated
  time is skipped this way, which makes the season run ~2x faster without
  changing game logic (the game only sees time pass).
* **INT 21h AH=2Ch hundredths** are derived from the tick count. The first
  lockstep comparison of the two ports found that the GDScript port returned
  0 here while the oracle returned hundredths; both now compute them the same
  way.
* **Keyboard.** Keys are injected as scan codes through IRQ1 and the
  emulated INT 09h, so Alt/Ctrl/Shift state lives in 0040:0017 and the BIOS
  key words match a real PC (Alt+E = 1200h). The next make code is held back
  until the BIOS buffer is empty, so scripted typing never overruns the game.
* **Extra-extra-fast mode** (Alt+E) is only accepted once the game screen is
  up: pressed during the title screens it is flushed. Scripts wait for the
  scoreboard text `Timeouts` first.
* **No config.nfl** → the configuration dialog; without it the team-disk
  prefix defaults to `b:`. Answer 4/4/1 to get `d:` (team disk on D:, colour,
  PC/XT). `config.nfl` produced: `Drive   Color   Delay   Special\r\nd:      1       100     0\r\n`.
* **season.nfl** written by SEASON.COM (run in the same emulator):
  `1\r\nBills Dolphins\r\n`.
* The program ends a season run with `INT 21h AH=4Ch AL=93h` (exit code 147);
  the stats file is complete, so this looks like an uninitialised return value.

## Results (Definition of Done, Phase 1)

| # | Item | Result | Evidence |
|---|------|--------|----------|
| 1 | first interactive screen | PASS | `build/shots/boot/01_config_team_disk.png` (+ `.txt`): CONFIGURATION dialog; `04_choose_home.png` team selection |
| 2 | configuration + roster | PASS | `config.nfl` written (above); `build/shots/boot/06_loading_roster.txt`: "Loading the team roster for the Bills..." |
| 3 | season end-to-end | PASS | SEASON.COM → `season.nfl`; `NFL /s` + Alt+E plays Dolphins at Bills to the end, writes `stats.log` (5034 bytes, full team and individual stats, `build/shots/season/stats.log`), exits to DOS |
| 4 | CGA frame | PASS | instant replay in mode 4: `build/shots/replay/cga_frame_0..3.png` |

CPU unit tests: `tools/refemu/test_cpu.py` (87 nasm-assembled snippets:
ADD/ADC/SUB/SBB/CMP/NEG/INC/DEC/logic/MUL/IMUL/DIV/IDIV incl. INT 0,
DAA/DAS/AAA/AAS/AAM/AAD, shifts/rotates, string ops with REP/overlap,
PUSHA/POPA/ENTER/LEAVE/PUSH imm/BOUND, jumps, LES, XLAT...). DOS test:
`tools/refemu/test_dos.py`.

## GDScript port verification (Phase 2)

No Godot binary could run here. Instead:

1. `gdparse` + `gdlint` on every `.gd` (from `godot/`: `gdlint emu/*.gd main.gd`).
2. `python3 -I tools/check_port.py`: compares opcode coverage (250 dispatched
   opcodes; 0F and 63–67 go to INT 6 in both), interrupt vectors, the AH/AL
   constants of every service function, I/O ports and function names.
3. `python3 -I tools/gdport/test_gdport.py --game-dir <nfl>`: translates the
   GDScript to Python (`tools/gdport/gd2py.py`, test only) on top of a shim of
   the Godot API (`tools/gdport/gdrt.py`: FileAccess, DirAccess, Time,
   String/Array/Dictionary/Packed*Array methods), runs the 87 CPU tests on the
   GDScript CPU, then runs NFL.EXE in both emulators in lockstep, feeding the
   same keys at the same emulated tick, comparing all registers and flags every
   1000 instructions, the full 1 MB memory every 5M instructions and the screen
   text and the rendered pixels (GDScript `Video.render()` vs the oracle's
   renderer) at every scripted wait. A third check drives the GDScript machine
   the way `main.gd` does in realtime mode (ticks from a simulated clock,
   `run(4000)` until idle) until the first screen appears.

Results (`build/gdport_diff.log`, `build/gdport_season.log`):

* 87/87 CPU tests pass on the GDScript CPU.
* Realtime loop: CONFIGURATION screen after 526 simulated frames (176 ticks,
  1.56M instructions; the CPU was idle in 511 frames), rendered at 640x400.
* Fresh start, 25M instructions: configuration dialog (config.nfl written),
  team selection, coach selection, "Loading the team roster": registers,
  memory, screen text and pixels identical at every check.
* `NFL /s` with SEASON.NFL and Alt+E: **110,428,077 instructions identical**,
  both programs exit, and both write a **byte-identical STATS.LOG**
  (4559 bytes, sha1 `c655df08e9cf94f3...`).
* CGA renderer (modes 4/5/6, several palette registers) on random video
  memory: pixels identical to the oracle.

The lockstep comparison found and fixed two real port bugs: INT 21h AH=2Ch
hundredths (see Quirks) and CP437 control-character glyphs in the text dump.

What this cannot catch: Godot API behaviour that the shim models wrongly,
GDScript typing errors that only the real parser/analyzer reports, and
everything in `main.gd`.

## Performance

Host: this sandbox, CPython 3.13, no PyPy.

| Run | Executed instructions | Wall time | Rate |
|-----|-----------------------|-----------|------|
| boot to configuration dialog, team select, ~2.5 game minutes | 156.5M (400M emulated incl. idle skips) | 187.5 s | 834k instr/s |
| `NFL /s`, one full game, extra-extra-fast | 73.9M (106M emulated) | 95.9 s | 771k instr/s |
| SEASON.COM (Turbo Pascal) | 1.8M | 1.7 s | 1.06M instr/s |

Emulated speed: 20,000 instructions per 18.2 Hz tick (`--ipt`), about
0.36 MIPS, close to a 4.77 MHz PC/XT. In normal (not extra-fast) mode a game
takes roughly 75,000 ticks (~70 emulated minutes, ~15 min host time); in
extra-extra-fast mode it takes ~4,300 ticks.

## Reproduce

```
G=/path/to/nfl                         # folder with NFL.EXE (read only)
python3 -I tools/refemu/test_cpu.py
python3 -I tools/refemu/test_dos.py
python3 -I tools/refemu/scenarios.py --game-dir $G boot season replay
python3 -I tools/refemu/run.py --root build/run-season --drive D=build/run-season/89 \
    --args /s --keys "{when:Timeouts}{wait:20}{Alt-E}" --max-minstr 200 --trace-ints
python3 -I tools/check_port.py
python3 -I tools/gdport/test_gdport.py --game-dir $G --minstr 25
python3 -I tools/gdport/test_gdport.py --game-dir $G --minstr 200 \
    --season build/run-season/89/SEASON.NFL
(cd godot && gdlint emu/*.gd main.gd)
```

`run.py` key scripts: plain characters are typed; `{Enter}` `{Esc}` `{F1}`
`{Alt-E}` `{Ctrl-C}` `{Down}` ... are keys; `{wait:N}` waits N ticks;
`{when:TEXT}` waits until TEXT is on the text screen; `{shot:NAME}` saves
`NAME.png` and `NAME.txt`.

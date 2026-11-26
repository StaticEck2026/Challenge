# Emulation notes

How `NFL.EXE` is run: by the Python reference emulator (`tools/refemu/`, the
"oracle"), and in Godot as **statically recompiled GDScript** on a native host
layer (`godot/host/`, see `docs/RECOMPILATION.md` and the section "Static
recompilation" at the end). The GDScript interpreter that `godot/emu/` used
to contain was retired to `tools/gdport/legacy_emu/`. It is kept only for
reference and for the speed comparison, and the sections about the "GDScript
port" below describe it. This file lists
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

## Static recompilation (current Godot build)

The Godot project runs GDScript produced by `tools/recomp/recompile.py` from
the user's `NFL.EXE`. The run produces 759 functions, 97,231 translated
instructions and 14,044 basic blocks, written to 23 scripts (about 494,000
lines, 10 MB) in about 2 s. Everything below was run in this sandbox. The
code under test is the generated GDScript plus `godot/host/*.gd`, translated
to Python by `tools/gdport/gd2py.py`. No Godot binary was available.

| Check | Command | Result |
|-------|---------|--------|
| Coverage | `coverage.py build/recomp/profile_{boot,season,replay}.json` | 315,902 executed (variant, address) pairs over the three profiles. The replay profile repeats the boot run, so 145,545 pairs are unique (57,015 distinct addresses). **100.000% of them are covered** by translated code, 0 missing. The natively replaced code is counted separately: ROM stubs, the PLINK86 manager (6,729 addresses), the thunks and the two switch helpers |
| Instruction semantics | `test_translate.py --count 3000 --length 20 --seed 11` | 3,000/3,000 random snippets agree with the oracle CPU (all registers, the 6 arithmetic flags, DF, data and stack memory, all flags live at the end). The test found one translator bug (a fused `xor` + `jp/jnp` emitted the XOR twice), now fixed. A mutation (a wrong OF formula) is detected |
| Season | `lockstep.py season` | `NFL /s`, extra-extra-fast, the whole game to its exit (code 147 on both sides): **identical at all 728,068 service calls and 364 memory checkpoints; `STATS.LOG` byte-identical** (4,991 bytes, sha1 `09d8fd11b542...`). `CONFIG.NFL` and the 77 graphics-mode ticks (title screen) are identical too, with 0 dispatch misses and 33 overlay loads |
| Boot + instant replay | `lockstep.py boot` | `NFL /i` without `config.nfl`: the CONFIGURATION dialog, team selection, coach selection, roster load, then 8,000 ticks of a game with instant replay. **Identical at all 541,803 service calls, 270 memory checkpoints and 9 script screens. The video memory was identical at all 4,137 ticks spent in CGA mode 4** (title and instant-replay field view). `CONFIG.NFL` byte-identical, 0 dispatch misses, 43 overlay loads. Frames from the recompiled run are in `build/recomp/shots-boot/` |
| Thread model | `thread_smoke.py` | the realtime clock on a Python thread with a 60 Hz main loop: CONFIGURATION after 2.5 s, team selection, roster load and the game screen (`Timeouts`) after 8.5 s (speed 4x); `request_abort()` joined the program thread at once |
| GDScript syntax | `gdparse`; `gdlint` (`godot/`: `main.gd text_screen.gd host/*.gd`; `godot/recomp/`: relaxed `gdlintrc`) | all files pass |
| No interpreter | `grep -rn "match op\|fetch8\|exec_op\|modrm" godot/ --include=*.gd` | no matches |

**Deterministic clock for lockstep.** The oracle's timer IRQ fires at
arbitrary instructions, which a recompiled program cannot reproduce. Both
sides therefore advance the BIOS tick every 64 polls (service calls through a
ROM stub, plus port reads) and whenever a service blocks. Keys are delivered
at ticks through the BIOS keyboard handler logic. The oracle's idle
fast-forward heuristic is off.

**Quirks found while bringing it up:**
* `13F3:0E6D` is a call-through-far-pointer trampoline (pop the return
  address and the far pointer argument, push them back swapped, RETF). The
  profile shows its RETF going to a function, so the translation makes it a
  dispatch.
* The soft-float package saves registers with helpers that return through
  `jmp [bp+10h]`, and its routines RETF past the helper's frame. The
  translation propagates such returns upward (stack unwinding) until a frame
  expects the address.
* The PLINK86 start-up leaves BP, SI, DI and DX holding values from loading
  overlay 12 (the last fixup offset, 0090h, 00C0h and the load segment) when
  it jumps to the C start-up. The native start-up sets the same values.
* Uninitialised fields of the `REGS` struct passed to `int86()` hold stack
  residue. The original's residue there is the overlay manager's scratch
  values, which the native manager does not reproduce. These fields reach
  only registers that the services ignore. See "Limitations" in
  `docs/RECOMPILATION.md` for how the lockstep compare handles them.

**Speed** (CPython 3.13, this sandbox):

| Run | Time | Rate (original 8086 instructions) |
|-----|------|-----------------------------------|
| oracle (Python interpreter), `/s` game, 193.8M instructions, deterministic clock | 245.8 s | 0.79 M/s |
| recompiled GDScript translated to Python, same game | 48.8 s | 4.0 M/s (**5.0x the interpreter**) |
| oracle, boot scenario (175.7M instructions) | 227.2 s | 0.77 M/s |
| recompiled, boot scenario | 42.5 s | 4.1 M/s (5.3x) |
| retired GDScript interpreter translated to Python (`legacy_emu/`), first 2.1M instructions of `/s` | 3.8 s | 0.55 M/s (the recompiled code is about 7x faster) |

The recompiled run spends most of its time in the game's own busy-wait: it
polls `int86(21h, AH=2Ch)` (and the ROM stub, service and dispatch around
it) hundreds of thousands of times. With the realtime clock in Godot those
loops simply run until real time has passed. GDScript speed was not
measured.

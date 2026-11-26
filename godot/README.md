# NFL Challenge in Godot 4 (PC emulator in GDScript)

This Godot 4.3+ project runs the **original, unmodified** DOS program `NFL.EXE`
(XOR Corporation, *NFL Challenge* v2.01, 1989) by emulating the machine it was
written for: an 8086/80186 CPU, the parts of the PC BIOS, DOS 3.30 and CGA
hardware that the program uses, all in GDScript (`emu/`). There is no game code
or game data in this project. **Bring your own copy of the game.**

> **Status: the GDScript build was NOT executed in the development sandbox**
> (no Godot binary could be downloaded or run there). What was verified:
>
> * every `.gd` file parses with `gdparse` and passes `gdlint` (relaxed limits in
>   `gdlintrc`, because the emulator core mirrors the Python reference emulator
>   one function per opcode group / service);
> * `tools/check_port.py` confirms that every opcode, interrupt vector, INT
>   sub-function (AH/AL) and I/O port handled by the Python reference emulator
>   (`tools/refemu/`) is handled in the GDScript port too;
> * `tools/gdport/test_gdport.py` transpiles `emu/*.gd` to Python (a test-only
>   GDScript subset translator plus a shim of the Godot API calls used) and
>   (a) runs the 87 CPU unit tests against the GDScript CPU, (b) runs `NFL.EXE`
>   in the GDScript machine in lockstep with the Python oracle, comparing
>   registers/flags every 1000 instructions and the whole 1 MB of memory every
>   5M instructions. They were identical through the configuration dialog,
>   `config.nfl` creation, team selection and roster loading, and through a
>   complete `/s` season game (110M instructions; both wrote a byte-identical
>   `stats.log`). The GDScript renderer produced the same pixels as the oracle.
>   See `docs/EMULATION_NOTES.md`.
>
> `main.gd` (window, input, rendering, audio, file dialog) uses Godot APIs that
> could not be exercised at all. Expect small fixes on the first real run.

## Install the game files

1. Copy the contents of your NFL Challenge disks into `godot/dos_c/`
   (the folder is in `.gitignore`), keeping the team disk in a sub-folder:

   ```
   godot/dos_c/NFL.EXE
   godot/dos_c/A ... Q            (program data files)
   godot/dos_c/LEAGUE.NFL
   godot/dos_c/SEASON.COM         (optional, season file builder)
   godot/dos_c/89/                (team disk: *.NFL, *.ROS, PBP.NFL, PLYB.NFL, PLS.NFL, CURVES.NFL)
   ```

   Other places that are searched, in this order:
   `--dos-root=<path>` on the command line, `user://dos_c/`
   (e.g. `~/.local/share/godot/app_userdata/NFL Challenge (PC emulator)/dos_c`),
   then `res://dos_c/`. You can also press **Game folder...** in the running app
   and pick any folder that contains `NFL.EXE`. If nothing is found, the app
   shows these instructions on screen.

2. Open the `godot/` folder in the Godot 4.3+ editor once (it imports the
   scripts and registers the `class_name`s), then press **Play**.

### Drives

| DOS drive | Host folder |
|-----------|-------------|
| `A:` `B:` `C:` | the game folder (`NFL.EXE` lives here; `C:` is current) |
| `D:` | `<game folder>/89` (the team disk; choose another with `--team-dir=84` or `--team-dir=Greats`) |

On the first start the game has no `config.nfl` and runs its CONFIGURATION
dialog. Answer **4** (team disk on drive D), **4** (colour graphics display),
**1** (PC/XT). The game writes `config.nfl` (see "Writes" below).

### Writes

The game writes `config.nfl`, `stats.log`, `avgstats.log` and saved games.
When the game folder is `res://dos_c` (read-only in exported builds), writes go
to `user://dos_c/` instead: new files are created there, and an existing file
is copied there before it is opened for writing; reads look in `user://dos_c/`
first. A folder given with `--dos-root` or picked in the app is written in
place. Printer output goes to `user://printer.txt`.

## Command-line options

Pass them after `--`, e.g. `godot --path godot -- --args=/s`:

| Option | Meaning |
|--------|---------|
| `--dos-root=<path>` | folder with `NFL.EXE` |
| `--args=<tail>` | DOS command tail for the program: `/i` instant replay on, `/s` season mode (reads `season.nfl` from the team disk, computer coaches, writes `stats.log`, exits), `/r` restore a saved game, `/l` log mode |
| `--team-dir=<sub>` | sub-folder mapped as drive `D:` (default `89`) |
| `--program=<file>` | run another program, e.g. `--program=SEASON.COM` to build `season.nfl` (answer `D` for the team-disk drive) |
| `--turbo` | start in turbo speed |
| `--mute` | no PC-speaker sound |
| `--fullscreen` | start in fullscreen |
| `--retro` | start with the original 8x16 bitmap text instead of HD text |
| `--modern-colors` | start with the modern palette |

## Keys

Every key is passed to the emulated keyboard as a PC/XT scan code (make and
break), so the emulated BIOS produces the same ASCII/scan words as a real PC,
including **Shift**, **Ctrl** and **Alt** combinations (for example **Alt+E**
"extra-extra-fast", **Alt+L** log mode, **Alt+T** clock, **Alt+S** save at
half time), **F1-F10**, arrows, **PgUp/PgDn**, **Home/End**, **Ins/Del** and
the numeric keypad (the game's two coaches use the left and right halves of
the keyboard). The game's own keys are described in its manual and in
`ADDENDUM.DOC`.

Front-end keys and buttons. The toolbar floats over the top of the screen and
fades out; move the mouse to the top edge to show it (it stays visible while
paused).

| Key / button | Action |
|--------------|--------|
| **F12** / *Speed* | cycle 1x, 2x, 4x, 8x, turbo. At 1x-8x the 18.2 Hz PC timer follows real time (times the factor); in turbo the timer is derived from the instruction count and the CPU runs flat out |
| **F11** / *Fullscreen* | toggle fullscreen (F11 is not passed to the game: it has no use for it) |
| *Text: HD / retro* | HD vector-font text (default) or the original 8x16 bitmap text, 4:3 |
| *Colors: classic / modern* | the 16 CGA colours, or a softer modern palette with the same hues (HD text only) |
| *Pause* | pause / resume emulation |
| *Reset* | reboot the program |
| *Game folder...* | pick the folder with `NFL.EXE` |
| *Screenshot* | save what is on screen to `user://screenshot_<time>.png` |
| *Sound* | toggle the PC speaker |

## Display: HD text at 1080p

The window opens maximized at 1920x1080 (stretch mode `disabled`, so nothing
is scaled after rendering: everything is drawn at the window's real pixel size).

* **Text screens** (menus, play calling, play-by-play, stats) are drawn by
  `text_screen.gd` with a vector font, JetBrains Mono, with DejaVu Sans Mono as
  the fallback for PC symbols. The font is rasterised at the actual cell size.
  The 80x25 grid fills the whole 16:9 screen: 24 x 43 px cells at 1920x1080,
  with the font sized to the cell width so letters keep natural monospace
  spacing.
* **Box-drawing, block and shade characters** are drawn as pixel-snapped
  rectangles spanning the full cell, so single and double frame lines join
  seamlessly at any size. Shades are blended colour fills rather than dither
  patterns.
* **CGA graphics** (instant replay, mode 4) stay pixel art: 320x200 scaled
  with nearest-neighbour filtering, 4:3 and pillarboxed.
* Any window size works. The cell width is capped at 0.75 x the cell height,
  so ultrawide screens get side margins instead of stretched text.

`tools/preview_hd.py` mirrors `text_screen.gd` (same layout and box rules,
drawn with Pillow) and renders real game screens at 1920x1080 without Godot:
`python3 -I tools/preview_hd.py --game-dir /path/to/nfl [--modern]` writes
`build/shots/hd/`.

## How it works

```
main.gd            front-end: frame loop, timer ticks, Image/ImageTexture output (4:3), keyboard, audio
emu/cpu.gd         8086 + 80186 CPU (two-level match dispatch, eager flags, F1 nn emulator trap)
emu/machine.gd     memory map, BIOS ROM stubs (F1 nn CF per vector), IVT, 8259 PIC, 8253 PIT,
                   CGA status/mode ports, keyboard controller, speaker
emu/bios.gd        INT 08/09 (tick, keyboard -> BDA ring buffer), 10h video (text + CGA 4/5/6),
                   11h 12h 13h 14h 15h 16h 17h 1Ah
emu/dos.gd         MZ/COM loader, PSP, environment, MCB chain, INT 20h/21h (handles, FCBs,
                   memory, find first/next, console), case-insensitive 8.3 drive mapping
emu/video.gd       incremental renderer (only changed cells/bytes are redrawn) + text dump
emu/font_cp437.gd  8x8 / 8x16 CP437 bitmap font (see docs/FONT_LICENSE.txt)
```

Each module is a one-to-one port of the Python reference emulator in
`tools/refemu/` (same function names), which is what was run and tested.
`NFL.EXE` calls the BIOS through a C-runtime `int86()` that builds an interrupt
frame and jumps through the vector table, so services are implemented where
the vectors point (ROM stubs containing the `F1 nn` trap), not by intercepting
`INT` opcodes.

## Performance

The Python reference emulator executes about 0.8M instructions per second
under CPython; a full computer-vs-computer season game in extra-extra-fast mode
is about 74M executed instructions. GDScript speed was not measured (no
Godot binary); it is expected to be of the same order, which is about the
speed of the 4.77 MHz PC the game was written for. If the game feels slow, use
**F12** to raise the speed. The HUD shows the measured instructions per second.

## Known gaps

* Not run in Godot (see the status box above); `main.gd` is untested.
* Exported builds: Godot only packs resource files by default, so put the game
  files in `user://dos_c/` or use `--dos-root` instead of `res://dos_c/`.
* Truncating a file with a zero-length write (`INT 21h AH=40h CX=0`) is ignored
  (the game does not use it).
* No 8087 (the game only probes for one), no EGA/VGA (the game uses CGA or MDA
  text and CGA mode 4), no mouse, no disk BIOS (INT 13h fails cleanly).

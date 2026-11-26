# NFL Challenge in Godot 4 (statically recompiled)

This Godot 4.3+ project runs the DOS program `NFL.EXE` (XOR Corporation,
*NFL Challenge* v2.01, 1989) as **GDScript recompiled ahead of time from your
own copy**. The translator `tools/recomp/recompile.py` turns every 8086
function of the program into a GDScript function in `recomp/`. The scripts in
`host/` provide what the program expects from the PC: DOS file services on
`FileAccess`, the BIOS keyboard and video services, the PLINK86 overlay
manager, the clock and the PC speaker. `main.gd` and `text_screen.gd` draw the
screen and take your keys. There is no 8086 interpreter, and no game code or
game data, in this project. **Bring your own copy of the game.**
`docs/RECOMPILATION.md` explains the design.

> **Status: this Godot project was NOT executed in the development sandbox**
> (no Godot binary could be downloaded or run there). What was verified:
>
> * every `.gd` file, the generated ones included, parses with `gdparse` and
>   passes `gdlint` (`gdlintrc` here; the recompiler writes a relaxed
>   `recomp/gdlintrc` for machine-written code);
> * `tools/recomp/lockstep.py` translates `host/*.gd` and the generated
>   `recomp/*.gd` to Python (`tools/gdport/gd2py.py`, a test-only GDScript
>   subset translator, plus a shim of the Godot API calls used) and runs them in
>   lockstep with the Python oracle (`tools/refemu/`, the old interpreter)
>   under a shared deterministic clock. A complete `/s` season game was
>   identical at every one of 728,068 service calls and at every memory
>   checkpoint, and wrote a byte-identical `stats.log`. The boot scenario
>   matches as well: the configuration dialog, team selection, roster load and
>   a game with instant replay, with the CGA video memory identical at all
>   4,137 ticks in graphics mode. See `docs/EMULATION_NOTES.md`;
> * `tools/recomp/thread_smoke.py` runs the same code with the realtime clock
>   on a Python thread, driven by a 60 Hz "main thread" exactly as `main.gd`
>   drives it (keys through the mutex-guarded queue, wake-ups, abort and join).
>
> Godot itself never compiled the about 490,000 generated lines and never
> ran the `Thread`, `main.gd` (window, input, rendering, audio, file dialog) or
> `text_screen.gd`. Expect small fixes on the first real run.

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

2. **Recompile your NFL.EXE once** (Python 3, no other dependencies):

   ```sh
   python3 tools/recomp/recompile.py godot/dos_c/NFL.EXE
   ```

   This writes about 24 scripts (about 10 MB of GDScript) to `godot/recomp/`
   in a couple of seconds. The folder is in `.gitignore`. The code is derived
   from your copyrighted binary, so never commit or distribute it. Run the
   command again after updating the translator. Without `recomp/`, the app
   shows this instruction.

3. Open the `godot/` folder in the Godot 4.3+ editor once (it imports the
   scripts and registers the `class_name`s), then press **Play**. On start the
   program thread loads the recompiled scripts. The HUD says
   "loading recompiled code..." until it has compiled them.

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
| `--program=<file>` | name of the program file in the game folder (default `NFL.EXE`). Only `NFL.EXE` is recompiled, so other programs such as `SEASON.COM` cannot run in this build. Build `season.nfl` with SEASON.COM under DOS or in `tools/refemu/` (`scenarios.py season`), or write it by hand: the number of games, then one `Home Visitor` line per game, for example `1`, then `Bills Dolphins` |
| `--turbo` | start in turbo speed |
| `--mute` | no PC-speaker sound |
| `--fullscreen` | start in fullscreen |
| `--retro` | start with the original 8x16 bitmap text instead of HD text |
| `--modern-colors` | start with the modern palette |

## Keys

Every key is passed to the keyboard as a PC/XT scan code (make and break), so
the BIOS keyboard handler produces the same ASCII/scan words as a real PC,
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
| **F12** / *Speed* | cycle 1x, 2x, 4x, 8x, turbo (32x). The 18.2 Hz PC clock follows real time times the factor. The game paces itself only by that clock, so everything gets faster |
| **F11** / *Fullscreen* | toggle fullscreen (F11 is not passed to the game: it has no use for it) |
| *Text: HD / retro* | HD vector-font text (default) or the original 8x16 bitmap text, 4:3 |
| *Colors: classic / modern* | the 16 CGA colours, or a softer modern palette with the same hues (HD text only) |
| *Pause* | pause / resume the program (its thread waits) |
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
recomp/            GENERATED from your NFL.EXE by tools/recomp/recompile.py (gitignored):
                   u_r_N.gd root code, u_oNN_M.gd overlay N, recomp_index.gd dispatch table
main.gd            front-end: starts the program thread, renders, keyboard, audio, folder picker
host/recomp_host.gd   runtime: links the units, native PLINK86 start-up and overlay manager,
                   ROM-stub services (INT n and int86()), dispatcher for indirect transfers
host/recomp_unit.gd   base class of the generated scripts
host/cpu_state.gd  registers, flags and the 1 MB memory (no decoder)
host/machine.gd    ROM stubs/IVT/BDA, I/O ports, keyboard queue, clock, service dispatcher
host/bios.gd       INT 10h video (text + CGA 4/5/6), 16h keyboard (BDA ring buffer), 1Ah, ...
host/dos.gd        MZ loader, PSP, environment, MCB chain, INT 21h (handles, FCBs, memory,
                   find first/next, console), case-insensitive 8.3 drive mapping
host/video.gd      incremental bitmap renderer (CGA, retro text) + text dump
host/font_cp437.gd 8x8 / 8x16 CP437 bitmap font (see docs/FONT_LICENSE.txt)
```

The recompiled program runs on its own `Thread`. Its code is nested and
blocking: it waits for a key inside the C runtime, like the DOS program does.
When it waits, it sleeps on a `Semaphore` that the main thread posts every
frame and whenever a key arrives. The main thread renders video memory and
feeds keys through a mutex-guarded queue. Reset and quit stop the thread
cleanly. Details are in `docs/RECOMPILATION.md`.

## Performance

In the sandbox, the recompiled program translated to Python ran a complete
`/s` season game (194M 8086 instructions in the oracle) in 49 s, which is about
4 million original instructions per second. The Python interpreter (the
oracle) took 246 s. The retired GDScript interpreter, translated to Python the
same way, runs about 0.55 million instructions per second, which would be
about 350 s; that figure is extrapolated, not timed over the whole game.
GDScript speed was not measured (no Godot binary). It does not need to match:
the game is paced by the 18.2 Hz clock, and the original PC ran about 0.4
million instructions per second. **F12** raises the speed. The HUD shows the
service calls per second.

## Known gaps

* Not run in Godot (see the status box above); `main.gd` is untested.
* `recomp/` must be regenerated (one command, above) for every fresh checkout:
  it is not committed. The translator targets this exact `NFL.EXE` (v2.01,
  8/8/89). `main.gd` refuses a program that loads at another segment.
* A code path that the profiling runs never reached and that uses a new
  indirect jump target stops with `dispatch miss at SSSS:OOOO` on the HUD.
  See "Limitations" in `docs/RECOMPILATION.md`.
* Exported builds: Godot only packs resource files by default, so put the game
  files in `user://dos_c/` or use `--dos-root` instead of `res://dos_c/`.
* Truncating a file with a zero-length write (`INT 21h AH=40h CX=0`) is ignored
  (the game does not use it).
* No 8087 (the game only probes for one), no EGA/VGA (the game uses CGA or MDA
  text and CGA mode 4), no mouse, no disk BIOS (INT 13h fails cleanly).

# NFL Challenge in Godot 4 (statically recompiled)

This Godot 4.3+ project runs the DOS program `NFL.EXE` (XOR Corporation,
*NFL Challenge* v2.01, 1989) as **GDScript recompiled ahead of time from your
own copy**. The translator `tools/recomp/recompile.py` turns every 8086
function of the program into a GDScript function in `recomp/`. The scripts in
`host/` provide what the program expects from the PC: DOS file services on
`FileAccess`, the BIOS keyboard and video services, the PLINK86 overlay
manager, the clock and the PC speaker. `main.gd` takes your keys and draws the
screen in one of three views: **Modern** (default, `ui/`: every screen redrawn
with flat SVG vector art from the program's memory, presentation only),
**HD** (`text_screen.gd`: the text screens with a vector font) or **Classic**
(the original bitmap). There is no 8086 interpreter, and no game code or
game data, in this project. **Bring your own copy of the game.**
`docs/RECOMPILATION.md` explains the design.

> **Status: now run with the official Godot 4.3 binary** (Linux, headless and
> under Xvfb with Mesa llvmpipe OpenGL 3; no real GPU, sound card, mouse or
> keyboard): `tools/godot_check.sh` imports the project, compiles all 49
> scripts and passes the boot, seasoncom and season smoke tests, and the
> interactive Modern view was driven through whole scenarios with `--keys=`
> (screenshots in `build/godot/shots/`, see `docs/MODERN_UI.md`, "Verification
> in Godot 4.3"). Verified before Godot could run here:
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
> Not exercised in real Godot yet: a hardware GPU, audio output (the
> sandbox falls back to Godot's dummy audio driver), the file dialog, and a
> person with a real mouse and keyboard (clicks and keys were injected
> through Godot's input pipeline instead).

## Install the game files

`NFL.EXE` is needed **once**, as the recompiler's input. The running game
never opens it. It runs from the generated code and the baked program image,
and reads only the game's data files.

1. **Recompile your programs** (Python 3, no other dependencies). Pass the
   game folder to translate both `NFL.EXE` and `SEASON.COM`, or pass just
   `NFL.EXE`:

   ```sh
   python3 tools/recomp/recompile.py /path/to/nfl [--bundle-data]
   ```

   This takes a couple of seconds and writes `godot/recomp/` (and
   `godot/recomp/season/` for SEASON.COM):
   * about 24 scripts (about 10 MB of GDScript);
   * `program.bin` (about 300 KB): the MZ header values, the root image already
     relocated for the fixed load segment, and the file's overlay section. The
     native overlay manager reads that section through a read-only virtual
     `NFL.EXE`, so the file does not have to exist on any drive. Its SHA-1 must
     match `PROGRAM_SHA1` in `recomp_index.gd`, otherwise the HUD says to
     recompile;
   * with `--bundle-data`, `game/`: a copy of the game's data files, which is
     everything in the EXE's folder and its subfolders except programs
     (`.EXE`, `.COM`, `.BAT`).

   The folder is in `.gitignore`. Everything in it is derived from your
   copyrighted files, so never commit or distribute it. Run the command again
   after updating the translator. Without `recomp/` the app shows this
   instruction.

2. **Provide the data files** (`A`...`Q`, `LEAGUE.NFL` and the team folder;
   `NFL.EXE` is not needed). The first folder that contains `LEAGUE.NFL`
   wins, searched in this order:
   * `--dos-root=<path>` on the command line;
   * `user://dos_c/` (for example
     `~/.local/share/godot/app_userdata/NFL Challenge (PC emulator)/dos_c`);
   * `res://dos_c/`, that is `godot/dos_c/` (gitignored);
   * `res://recomp/game/`, written by `--bundle-data`.

   ```
   godot/dos_c/A ... Q            (program data files)
   godot/dos_c/LEAGUE.NFL
   godot/dos_c/89/                (team disk: *.NFL, *.ROS, PBP.NFL, PLYB.NFL, PLS.NFL, CURVES.NFL)
   ```

   You can also press **Game folder...** in the running app and pick any
   folder with the data files. If nothing is found, the app shows these
   instructions on screen.

3. Open the `godot/` folder in the Godot 4.3+ editor once (it imports the
   scripts and registers the `class_name`s), then press **Play**. On start the
   program thread loads the recompiled scripts. The HUD says
   "loading recompiled code..." until it has compiled them.

**Standalone export.** Recompile with `--bundle-data`. In the export preset,
under *Resources → Filters to export non-resource files/folders*, add
`recomp/*.bin, recomp/game/*`. Godot packs only resources by default, and
`program.bin` and the data files are plain files. The exported game then needs
nothing else. Saves, `config.nfl` and `stats.log` go to `user://dos_c/`.

### Drives

| DOS drive | Host folder |
|-----------|-------------|
| `A:` `B:` `C:` | the game data folder (`C:` is current). A read-only virtual `NFL.EXE` exists on every drive for the overlay manager |
| `D:` | `<game folder>/89` (the team disk; choose another with `--team-dir=84` or `--team-dir=Greats`) |

On the first start the game has no `config.nfl` and runs its CONFIGURATION
dialog. Answer **4** (team disk on drive D), **4** (colour graphics display),
**1** (PC/XT). The game writes `config.nfl` (see "Writes" below).

### Writes

The game writes `config.nfl`, `stats.log`, `avgstats.log` and saved games.
When the game folder is `res://dos_c` or `res://recomp/game` (read-only in exported builds), writes go
to `user://dos_c/` instead: new files are created there, and an existing file
is copied there before it is opened for writing; reads look in `user://dos_c/`
first. A folder given with `--dos-root` or picked in the app is written in
place. Printer output goes to `user://printer.txt`.

## Command-line options

Pass them after `--`, e.g. `godot --path godot -- --args=/s`:

| Option | Meaning |
|--------|---------|
| `--dos-root=<path>` | folder with the game's data files (`NFL.EXE` not needed) |
| `--args=<tail>` | DOS command tail for the program: `/i` instant replay on, `/s` season mode (reads `season.nfl` from the team disk, computer coaches, writes `stats.log`, exits), `/r` restore a saved game, `/l` log mode |
| `--team-dir=<sub>` | sub-folder mapped as drive `D:` (default `89`) |
| `--program=season` | run **SEASON.COM** (the season file manager) instead of NFL.EXE. It writes `season.nfl` to the team drive (`D:`, writable), and the next start with `--args=/s` plays that schedule. Needs `res://recomp/season/` from `recompile.py <game folder>` |
| `--smoke=boot\|seasoncom\|season` | self-test, normally run headless by `tools/godot_check.sh` (see "Check it in Godot"): runs the program on a deterministic key script, saves screenshots, prints timing, and quits with exit code 0 or 1. `--smoke-out=<dir>` (default `user://smoke`), `--smoke-timeout=<seconds>` (default 600) |
| `--turbo` | start in turbo speed |
| `--mute` | no PC-speaker sound |
| `--fullscreen` | start in fullscreen |
| `--view=modern\|hd\|classic` | start in the Modern view (default), HD text, or the original bitmap |
| `--retro` | same as `--view=classic` |
| `--field=horizontal\|vertical` | Modern view field orientation (default horizontal; the toolbar choice is remembered in `user://modern_view.cfg`) |
| `--modern-colors` | start with the modern palette |
| `--keys=<script>` / `--keys-file=<file>` | drive the game with a key script (`host/key_script.gd` syntax: `{when:TEXT}`, `{wait:N}`, `{Enter}`, `{Alt-E}`, ...; `{when:[graphics mode 4]}` waits for a play) plus `{shot:NAME}` (save the window to `--shot-dir`) and `{click:ACTION}` (click the Modern view's button with that action, e.g. `ui:zoom`, `key:PgDn`, `pad:R:3`; `hostkey:KP_2` sends a numeric-keypad key) |
| `--shot-dir=<dir>` | where `{shot:}` saves (default `user://shots`) |
| `--quit-when-done` | quit when the key script is done; prints frame times and the Modern view's own time per frame |
| `--view-trace=<file>` | one line per frame during plays (camera, first discs) for smoothness checks |

## Keys

Every key is passed to the keyboard as a PC/XT scan code (make and break), so
the BIOS keyboard handler produces the same ASCII/scan words as a real PC,
including **Shift**, **Ctrl** and **Alt** combinations (for example **Alt+E**
"extra-extra-fast", **Alt+L** log mode, **Alt+T** clock, **Alt+S** save at
half time), **F1-F10**, arrows, **PgUp/PgDn**, **Home/End**, **Ins/Del** and
the numeric keypad (the game's two coaches use the left and right halves of
the keyboard: the left coach answers his menus with F1-F10, the right coach
with the keypad digits; with your NumLock on, a numeric-keypad key reaches
the program as its digit, sent with Shift held, which is how the host BIOS
gives keypad digits). The game's own keys are
described in its manual and in `ADDENDUM.DOC`.

Front-end keys and buttons. The toolbar floats over the top of the screen and
fades out; move the mouse to the top edge to show it (it stays visible while
paused).

| Key / button | Action |
|--------------|--------|
| **F12** / *Speed* | cycle 1x, 2x, 4x, 8x, turbo (32x). The 18.2 Hz PC clock follows real time times the factor. The game paces itself only by that clock, so everything gets faster |
| **F11** / *Fullscreen* | toggle fullscreen (F11 is not passed to the game: it has no use for it) |
| *View: Modern / HD / Classic* | the Modern vector view (default), HD vector-font text, or the original bitmap (8x16 text and CGA), 4:3 |
| *Colors: classic / modern* | the 16 CGA colours, or a softer modern palette with the same hues (HD text only) |
| *Pause* | pause / resume the program (its thread waits) |
| *Reset* | reboot the program |
| *Game folder...* | pick the folder with the game's data files |
| **Print Screen** / *Screenshot* | save what is on screen to `user://screenshot_<time>.png` |
| *Sound* | toggle the PC speaker |

## Display: the Modern view

The default view redraws every screen with hand-authored flat SVG art
(`ui/art/`), rasterised with `Image.load_svg_from_string()` at the window's
size (sharp at any resolution):

* the field top-down, whole: horizontal filling 16:9 (default), or vertical
  filling the height with the scoreboard, play lists and play text in the
  side columns (toolbar *Horizontal/Vertical*, `--field=`, remembered per
  viewer); striped grass, yard lines and numbers, hash marks, goal posts, end
  zones in the teams' own colours;
* players as team-coloured discs with their jersey numbers (from the game's
  rosters; if the colours clash only the visitor's discs change colour), moving smoothly between the game's position updates; the ball,
  the line of scrimmage (blue) and the first-down line (yellow), for plays,
  instant replay and the field overview before the snap;
* *Zoom*: an auto-framing camera for plays that eases to the largest view
  holding all 22 players and the ball (up to 2.2x, down to the whole field
  when they spread out), in both orientations; the scoreboard pill turns
  compact and translucent and never covers a player;
* a scoreboard pill (team codes, score, quarter, clock, time-outs,
  possession, down and distance), a slim toolbar (View, Speed, Pause, Sound,
  Screenshot, field orientation, Zoom, More, Fullscreen) and an info strip
  with the play text;
* setup, team selection, match-up, play calling (also a human coach's
  plays, kick, fourth-down and penalty menus), team and per-player
  statistics and prompts as cards and buttons. A button sends the key the
  screen asks for; a human coach's menu entry sends that coach's own key
  (F1-F10 or keypad). Screens it does not recognise (save game, exit) show
  the original text (HD renderer) in a modern frame.

Players are drawn for every play without `/i` (`/i` only adds the instant
replay); in extra-extra-fast mode (Alt+E) the game computes no positions,
so the view shows the ball marker and the play text.

It reads the program's memory only (addresses in `ui/state_map.json`) and
never writes it. The *More* button shows the toolbar with Reset, Game folder
and Colors. The art files have `importer="keep"` import settings, so Godot
exports them as plain files. Design, address map and its evidence:
`docs/MODERN_UI.md`.

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
ui/                the Modern view: game_state.gd (reads memory, state_map.json), screens.gd
                   (classifier + layouts), match_view.gd (field), draw_list.gd, art.gd, ui_theme.gd,
                   modern_view.gd (Godot drawing and input), art/*.svg
recomp/            GENERATED from your NFL.EXE by tools/recomp/recompile.py (gitignored):
                   u_r_N.gd root code, u_oNN_M.gd overlay N, recomp_index.gd dispatch table,
                   program.bin baked program image, game/ data files (--bundle-data)
host/program_blob.gd  reads program.bin;  host/mem_file.gd  the virtual NFL.EXE
main.gd            front-end: starts the program thread, renders, keyboard, audio, folder picker
host/recomp_host.gd   runtime: links the units, native PLINK86 start-up and overlay manager,
                   ROM-stub services (INT n and int86()), dispatcher for indirect transfers
host/recomp_unit.gd   base class of the generated scripts
host/cpu_state.gd  registers, flags and the 1 MB memory (no decoder)
host/machine.gd    ROM stubs/IVT/BDA, I/O ports, keyboard queue, clock, service dispatcher
host/bios.gd       INT 10h video (text + CGA 4/5/6), 16h keyboard (BDA ring buffer), 1Ah, ...
host/dos.gd        program set-up from program.bin, PSP, environment, MCB chain, INT 21h (handles, FCBs, memory,
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

## Check it in Godot (one command, on your machine)

```sh
tools/godot_check.sh [--game-dir /path/to/nfl]
```

The script does the following:
* downloads the official **Godot 4.3-stable** for your platform from
  `github.com/godotengine/godot-builds` (once, into `build/godot/`) and
  verifies it against the release's `SHA512-SUMS.txt`;
* recompiles if `godot/recomp/` is missing (with `--game-dir`);
* runs `godot --headless --path godot --import`;
* compiles every script and reports failures
  (`--script res://host/check_scripts.gd`, which also prints the compile
  time);
* runs the smoke mode for `boot`, `seasoncom` and `season`, with screenshots
  in `build/godot/smoke/`.

It exits non-zero if any step fails. Data files come from `--game-dir`, or
from the bundled `recomp/game/` (`recompile.py ... --bundle-data`).

* **Linux** (x86_64, arm64): run it as shown. It needs `curl`, `unzip` and
  `sha512sum` (coreutils).
* **macOS**: run it as shown (it uses `shasum -a 512` and the universal
  `Godot.app`). If Gatekeeper blocks the downloaded binary, run
  `xattr -dr com.apple.quarantine build/godot/Godot.app` once.
* **Windows**: run it from **Git Bash** (or MSYS2), which provides bash, curl,
  unzip and sha512sum. It uses `Godot_v4.3-stable_win64_console.exe` so the
  output reaches the terminal. Without Git Bash, do the steps by hand:
  download and verify the zip, then run
  `Godot_v4.3-stable_win64_console.exe --headless --path godot --import`,
  then `... --script res://host/check_scripts.gd`, then
  `... -- --smoke=boot` (and `seasoncom`, `season`).
* Already have Godot 4.3? Pass `--godot /path/to/godot`.

## Known gaps

* Run in Godot 4.3 only in a headless/Xvfb software-rendering sandbox (see the
  status box above): no real GPU, audio device or human input yet.
* `recomp/` must be regenerated (one command, above) for every fresh checkout:
  it is not committed. The translator targets this exact `NFL.EXE` (v2.01,
  8/8/89).
* SEASON.COM runs with `--program=season`, and other DOS programs (the
  roster editor `RE.COM`, `XORLIST.EXE`) are not translated.
* A code path that the profiling runs never reached and that uses a new
  indirect jump target stops with `dispatch miss at SSSS:OOOO` on the HUD.
  See "Limitations" in `docs/RECOMPILATION.md`.
* Exported builds: Godot only packs resource files by default. Add the
  non-resource filter above (`recomp/*.bin, recomp/game/*`), or put the data
  files in `user://dos_c/` or use `--dos-root`.
* Truncating a file with a zero-length write (`INT 21h AH=40h CX=0`) is ignored
  (the game does not use it).
* No 8087 (the game only probes for one), no EGA/VGA (the game uses CGA or MDA
  text and CGA mode 4), no mouse, no disk BIOS (INT 13h fails cleanly).

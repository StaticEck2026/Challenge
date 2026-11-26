# Modern view: a vector-art presentation layer

The Godot project has three views, chosen with the toolbar's **View** button or
`--view=modern|hd|classic` on the command line:

| View | What it draws |
|------|---------------|
| **Modern** (default) | Every screen redrawn with flat SVG vector art in a 2D "match centre" style: the whole field top-down with the players as numbered team-coloured discs, a scoreboard pill, cards, lists and buttons. This document. |
| HD | The original text screens with a vector font at native resolution (`text_screen.gd`); CGA graphics as a bitmap. |
| Classic | The original bitmap for everything, 4:3. |

The Modern view is **presentation only**. The recompiled program stays the
single source of truth for rules, AI, play results and screens. The layer
reads the program's memory (the text page, the CGA frame, and the game
variables listed in `godot/ui/state_map.json`) and draws it. It never writes
memory, never calls into the program and does not change timing or the order
of service calls (proved by the lockstep test, below). Its buttons only inject
the key the screen asks for ("Press R for Right keypad" becomes an **R** button
that sends the R scancode, exactly like the key; a human coach's play list
sends that coach's own keys, see "Coach keys").

## Files

| File | Role |
|------|------|
| `godot/ui/state_map.json` | The address map: every game variable used, with type, meaning, evidence and confidence (facts about the binary, no game data) |
| `godot/ui/game_state.gd` | `GameState.update(mem)`: reads the text page, the BIOS video mode, the mapped variables and, during plays, the 22 players and the ball, converted to field yards |
| `godot/ui/screens.gd` | `Screens.classify()` recognises the screen from the text in video memory; `Screens.build()` lays it out (cards, lists, buttons); unrecognised screens fall back to the HD text renderer inside a modern frame |
| `godot/ui/match_view.gd` | The field view: field, end zones, yard numbers, line of scrimmage, first-down line, discs, ball, scoreboard pill, toolbar, info strip |
| `godot/ui/draw_list.gd` | The frame as a list of vector operations on a 1920x1080 logical canvas, and `to_svg()` |
| `godot/ui/art.gd` | Loads the SVG art and fills its `{{placeholders}}` |
| `godot/ui/ui_theme.gd` | Colours, plain per-team colour pairs (keyed by the short team name the game prints; no logos), disc "kits", team codes |
| `godot/ui/modern_view.gd` | The Godot node: runs the above every frame, rasterises the SVG art with `Image.load_svg_from_string()` at the window's scale (cached, re-rasterised on resize), draws shapes and text, interpolates player movement, handles clicks |
| `godot/ui/art/*.svg` | Hand-authored, original flat vector art: `field`, `token`, `token_ring`, `ball`, `line_scrimmage`, `line_first_down`, `scoreboard`, `card`, `panel`, `button`, `coin`, `mark`, `icon_*`. Their `.import` files say `importer="keep"`: Godot does not import them as textures (they contain placeholders) and exports them as plain files |
| `godot/fonts/Inter-*.otf` | UI font (SIL OFL 1.1, see `Inter-LICENSE.txt`) |
| `tools/modern/` | Test and preview tools (below) |

`main.gd` adds the view switch, the `--view=` flag, the Modern view's
toolbar actions, key injection for its buttons and the **Print Screen**
screenshot key. The program thread is unchanged.

## Design

* **Canvas.** All layout is on a 1920x1080 logical canvas, scaled uniformly to
  the window (letterboxed if the aspect differs). Art is rasterised at the
  final pixel size, so it is sharp at 1080p, 4K or any window size.
* **Field orientation.** Horizontal (default) or vertical, from the
  toolbar's orientation button or `--field=horizontal|vertical`; the choice
  is saved per viewer in `user://modern_view.cfg` (a missing or unreadable
  file just leaves the default). Everything that draws the field goes through
  one transform, `MatchView.pt(fx, fy)`: vertical turns the field 90 degrees
  counter-clockwise, the left goal line of the game's field at the bottom
  (the same way the game's own graphics field runs). Game-state positions and
  the position check are untouched. In vertical the field fills the height of
  the frame, centred; the scoreboard pill sits at the top of the left column,
  play-calling windows fill the left and right columns without covering the
  field, and the info strip becomes two cards at the foot of the columns
  (possession left, play text and line-up right). Yard numbers turn a quarter
  with their bottoms towards the nearer sideline, end-zone names are upright,
  disc numbers always stay upright.
* **Zoom camera** (toolbar *Zoom*, both orientations, plays only). An
  auto-framing camera: its target is the largest scale at which all 22
  players and the ball fit, with 3 yards of margin, in the area under the
  toolbar and the scoreboard pill and above the info strip
  (`MatchView.frame_area()`; the pill's height is kept free), capped at 2.2x
  the full-field scale and centred on that group (`frame_camera()`). The
  view eases towards the target with exponential smoothing (rate 2.5/s,
  `1 - exp(-2.5 dt)` per frame: no overshoot, no jitter from frame-rate
  changes), then `contain_camera()` corrects the eased camera so nobody is
  outside: the scale is never larger than the target allows and the centre
  moves just enough to bring every disc in. So the cap gives way only when
  the players spread out, down to the full field at most (a kick-off). The
  camera starts on the target at the first frame of a play and resets
  between plays. While zoomed the pill is compact (72 %) and translucent;
  discs never sit under it because its area is not part of the frame.
* **Field.** Horizontal, the whole 120 yards filling the 16:9 frame: mown
  stripes every 5 yards, yard lines, one-yard hash marks along the sidelines
  and on two inbound rows (placed where the game's own graphics field puts
  them, 21.6 and 31.75 yards from the top sideline), goal lines, end lines,
  goal posts seen from above, end zones in the colour of the team defending
  them with that team's name. Yard numbers (upright at the bottom, upside down
  at the top, with direction arrows) and end-zone names are drawn as text,
  because Godot's SVG rasteriser does not draw SVG `<text>`.
* **Players.** A disc per player in the team colour with a darker rim and
  the jersey number from the game's roster (position letters if a number is
  missing); the ball carrier gets a yellow ring and the ball sits at his
  side. Discs are 0.8 yd in radius on the full field and 0.9 yd with the zoom
  camera, never smaller than 10.5 px (the vertical full field is small). A display-only declutter (6 relaxation passes) pushes overlapping
  discs apart, at most half a disc radius (0.40 / 0.45 yd horizontal, up to
  0.66 yd on the vertical full field where the 10.5 px minimum applies), so
  numbers at
  the line stay readable; drawing order is defence, offence, ball carrier.
  The game's positions are not changed (the position check uses them).
  When the game switches to graphics for a play it first draws the field
  with the old records still in memory (about 1.3 s at 1x), then computes
  the line-up one player at a time; a record is shown only once the game has
  written it in this graphics session (its position differs from the one it
  had in text mode; `GameState` "placed"), so no stale disc flashes up. A
  player far out of bounds, beyond the margin the full field shows, is
  pinned to the edge of the view instead of leaving it.
* **Team colours** (`UiTheme.kit_colors`). End zones and their names,
  scoreboard blocks, the strip's team block, match-up cards and statistics
  pills always use each team's own primary colour. Only the player discs
  (on the field and as line-up chips) take a clash-resolved kit: the home
  team plays in its primary; if the visitor's primary is closer than 18
  (OKLab distance x100), the visitor's discs switch to its secondary colour,
  and if that clashes too, to the neutral kit (white, near-black, red,
  amber) farthest from the home colour. Text on any fill is white or
  near-black by WCAG contrast; on a white kit it is the team colour when that
  has at least 4.5:1 contrast. Colts at Bills: both end zones blue, Colts
  discs white with blue numbers. Discs move smoothly: the game updates
  positions a few times a second, the view interpolates between two updates
  over the measured update interval, at the display's frame rate. Jumps over
  9 yards (a new play) are not animated.
* **Lines.** Line of scrimmage blue, first-down line yellow (from the series
  start and the attacking direction; at the goal line when goal to go).
* **Scoreboard pill** at top centre: visitor block in team colour, score,
  quarter and clock, score, home block; a small ball marks possession, dots
  under the codes are the time-outs left, and a tab under the pill shows down
  and distance and the ball's spot ("2nd & 9 · COL 34"). It shows what the
  game's scoreboard shows: values are parsed from the text scoreboard while it
  is on screen and kept while a play is drawn (the game updates its variables
  to the play's result before showing the play; reading them directly would
  give the result away).
* **Toolbar** (slim, dark, top): View, Speed, Pause/Resume, Sound, Screenshot,
  field orientation (Horizontal/Vertical), Zoom (the auto-framing camera
  for plays; the default is the full field), More (shows the HD/Classic
  toolbar with Reset, Game folder and Colors), Fullscreen.
* **Info strip** (dark, bottom): the team with the ball and its time-outs,
  the play call and result text the game prints under its field ("HB Option
  Weak vs 30 Double Zone", "Run... Gained 1 yard. Carried by #20, RB ..."),
  and during plays the offence's 11 players as chips (number and position).
* **Screens.** Configuration (a card with option rows), team selection (AFC
  and NFC rows of division cards, the game's cursor highlighted), match setup
  (two team cards with coach and keypad, the prompt with key buttons, the coin
  toss), title and loading, the field overview with the game's windows as side
  panels (play lists, substitution groups, coach menu, kick, fourth-down and
  penalty menus; numbered entries become buttons that send the coach's keys,
  and a human coach's panel is tagged *F-keys* or *Keypad*), roster tables,
  notices (injuries, with the game's block-letter caption as a badge; the
  penalty description), y/n questions as Yes/No buttons, the W key's "Show
  play windows (L,R)" prompt as Left/Right toggles and Done, team statistics
  (section cards with comparison bars) and the per-player pages behind PgDn
  (Rushing, Passing, Receiving, ..., Sacks: two team cards with the game's
  column headers, one row per player and the totals), with
  PgUp/PgDn/Continue buttons.

## Game-state address map and evidence

DGROUP is the program's data segment, link segment `23D6`, run-time segment
`24F8` at the fixed load segment `0122` (linear `0x24F80`). The full list with
types is `godot/ui/state_map.json`; the table summarises it. "code" means
read from the disassembly (`tools/disasm`), "diff" found by memory diffing in
the recompiled run (`tools/modern/research.py`), "check" verified by
`tools/modern/check_positions.py`.

| DGROUP | Meaning | Evidence | Confidence |
|--------|---------|----------|------------|
| `57EA` | quarter | code: the save game writes it under "Quarter and time" (ovl 3 `203D:0413`) | high |
| `57EC` / `57EE` | team with the ball (1 home, 2 visitor) / defence | code: `0031:0D34` sets both; save game "tm w/ball" | high |
| `6719`, `6717` | clock minutes, seconds | code: `0AAB:0596`; diff: equal to the scoreboard clock in 11,054 text frames | high |
| `6715` | play clock (30..25 right of "Down:") | diff | high |
| `672F` | down | code: save game "down"; diff | high |
| `6733` | ball spot, yards from the left goal line of the text field; set to the play's result before the play is shown | code: `0BFF:0134` getter, `0BFF:0166` flips `100-x`; diff | high |
| `6731` | start of the series ("strt yd"); first down = start + 10 x direction | code: `0BFF:014D`; diff: To Go = 10 - distance | high |
| `5BF4` | line of scrimmage of the play being shown | diff: equals the pre-play spot at the first graphics frame of 11 plays; check | medium |
| `5BE0`, `5BE2` | attacking direction of home, visitor (+1 right, -1 left) | code: `0031:0D16` | high |
| `5BDC`, `5BDE` | time-outs left, home, visitor | code: `0031:0C66` | high |
| `6765 + 2q`, `676F + 2q` | points per period, home, visitor | code: `0C5F:0058`; diff against the box score | high |
| `4122`, `4124` | total points, home, visitor | code: `0C5F:0096` sums into them; diff | high |
| `672B`, `672D` | column/row of the ball marker on the text field (moves during kicks) | diff | high / medium |
| `688F`, `3CA4` | "sit" and "play" of the save game | code: `203D:052F`/`0533` | high / medium |
| `83CF`, `83DE` | home short name, display name | code: season.nfl `sscanf` target; diff | high / medium |
| `8400`, `840F` | visitor short name, display name | as above | high / medium |
| `516C`, `547A` | rosters: 49 records of 15 bytes (byte 0 jersey, byte 1 position code; the names OL C TE WR RB QB DL LB DB P K are at `090C`) | code: `0031:0C9E` returns `516A` / `5478` per team; diff against the team file | high |
| `546D`, `577B` | roster indexes of each team's 11 players on the field, in play-record order | diff: holds the formation's starters; check: C on the ball, QB behind him, "Carried by #20" is the disc with the ball | medium |
| `A494` | the graphics play: 23 records of (x, y, k) words; 0-10 offence (drawn as O), 11-21 defence (X), 22 the ball; CGA pixels of the sprite's top-left corner on the scrolled field | diff: equal to the X/O recognised in CGA memory at every frame; check | high |
| `83CB`, `83CD` | home coach (1 human, 2 computer), home coach's keyboard side (1 right = numeric keypad, -1 left = F-keys) | code: the save game writes them with the names under "Coach, Keypad, Team" (`12CA:0164`); diff; check: the human-coach captures | high |
| `83FC`, `83FE` | visitor coach, keyboard side | code: save game (`12CA:018C`); diff | high |
| `8753`, `8755` | horizontal and vertical field scroll (`field_x = x - cam_x`, `field_y = y + cam_y`; fraction bytes at `8752`/`8754`) | diff: change exactly when the drawn yard lines scroll; check | high |

Field geometry of the game's graphics view, measured from the lines it draws:
8 pixels per yard both ways, the CGA-left sideline at `field_x = -54`, yard
lines every 40 pixels. Yards from the left goal line are
`los + (Y - field_y - 4) / 8` with `Y` = 233 when the team with the ball
attacks to the right and 169 when it attacks to the left; across the field,
`(field_x + 4 + 54) / 8` yards from the top sideline. The game's vertical
field becomes the Modern view's horizontal field (turned 90 degrees, with
"up" in the game's view pointing right).

Not located: the play's frame counter (the view counts record updates
instead: `GameState.play_step`) and an explicit ball-carrier variable (the
carrier is the player whose record equals the ball's, which is how the game
draws it).

## Screens covered

Driven through the recompiled program in five scripted runs (CPU vs CPU
with instant replay, Colts at Bills and Dolphins at Bills; a human coach on
the right keypad up to his menu; a whole game at extra-extra-fast speed to
the "Another Game" question, paging through the statistics; and
`humanplay`: the Bills coached by a "human" on the right keypad for a
quarter, every choice made with the same keys the Modern view's buttons
inject):

| Screen | Modern layout | Preview |
|--------|---------------|---------|
| Title (asterisk art), loading | splash card with status rows | `09_loading` |
| Configuration: team disk, display, system | card with option buttons 1-5 | `01`-`03` |
| Choose the home / visiting team | AFC/NFC division cards, cursor highlighted, arrow/Enter/Esc buttons | `04`, `05` |
| Match-up: coach (C/H), keypad (R/L), coin toss | team cards, prompt with key buttons, coin | `06`-`08`, `30` |
| Field overview before a play / kickoff / after a play | field, ball spot, scrimmage and first-down lines, scoreboard, play text | `10`, `11`, `17` |
| Play calling (play lists, substitution groups, "Offense selected") | side panels with numbered buttons, status chips | `12` |
| Coach menu (human coach) | side panel with buttons | `31` |
| Plays and instant replay (CGA mode 4) | field with 22 discs and the ball | `13`-`16`, `18`-`20`, `14_play_mid_zoom` |
| Injury report, substitution roster | notice card with badge, roster table panel | `41` |
| Half-time and final team statistics | section cards with comparison bars, PgUp/PgDn/Continue | `40`, `42` |
| Per-player statistics behind PgDn (Rushing, Passing, ..., Sacks) | two team cards with column headers, player rows, totals; PgUp/PgDn/Continue | `40b`-`40d` |
| "Another Game (y/n)?" (and any other "(y/n)" question under the field: "Exit (y/n)?", "New game (y/n)?", "Do you wish to pick new teams (y/n)?") | Yes/No buttons | `43` |
| Human coach: kick-off menu (Normal / Onside / Squib) | side panel, keypad buttons, *Keypad* tag | `60` |
| Human coach: defence formations and plays, offence groups and plays | side panels with the coach's keys (F1-F10 left, keypad digits right) | `61`-`64` |
| Human coach: fourth down (Go for it / Punt / Field goal) | side panel with keypad buttons | `65` |
| Penalty: the description and "1 to accept / 2 to decline" | notice card over the field and a side panel with buttons | `66` |
| W: "Show play windows (L,R): LEFT On RIGHT On" | card with Left/Right toggle buttons (lit when On) and Done (Enter) | `67` |
| Anything else (the save-game and exit screens, messages not seen in the runs) | the original text (HD renderer) inside a modern frame | - |

Not reached in the scripted runs and therefore only covered by the fallback:
the save-game screens ("SAVE GAME", "Checking for room to save game...";
Alt+S, S and Ctrl+S at the half-time statistics of a CPU-vs-CPU game did
nothing) and the exit screen ("Press ... X ...to exit NFL Challenge":
answering N to "Another Game (y/n)?" ends the program directly). Their y/n
questions get Yes/No buttons when they appear under the field; the rest
shows as text in the modern frame. The toggles behind the Alt keys
("Enter Graphics mode", "Sound off", ...) are one-line messages printed in
the play-text rows, which the info strip shows as they are.

## Coach keys

The game reads the two coaches from the two halves of the keyboard: a coach
on the **left keypad** answers with the function keys (entry 1 = F1 ...
entry 10 = F10, scan codes `3B`-`44`), a coach on the **right keypad** with
the numeric keypad producing digits (entry 1-9 = keypad 1-9, entry 10 =
keypad 0; scan codes `4F 50 51 4B 4C 4D 47 48 49 52` with the ASCII digit,
which the host BIOS, `host/bios.gd`, gives with Shift held: it does not apply
NumLock to the keypad). The
plain digit keys are ignored in these menus. Which coach is human and on
which side is in memory (`83CB`/`83CD` home, `83FC`/`83FE` visitor; see the
address map).

A numbered entry in a play-calling window becomes a hotspot
`pad:<L|R>:<digit>`: the side is that of the team named in the window title
(or, for an untitled window, the screen side it is on), and the badge shows
the key (`F3` or `3`). `main.gd` (`_press_coach_key`) turns it into F1-F10,
or into the keypad scan code wrapped in Shift make/break. A real
numeric-keypad key that the host's NumLock makes a digit is sent the same way
(`_unhandled_input`). (An earlier version sent the plain keypad code when the
program's NumLock flag was on; real Godot showed that the host BIOS then
gives no digit, and the kick menu ignored the click.)

Verified in the `humanplay` capture: the bot plays the Bills (human, right
keypad) only through those injected keys, through the kick-off, defence,
offence, fourth-down and penalty menus, and checks that the play it called
is the one the game runs: entry 5 of the White group, "YZ Cross", was
followed by the game's own "YZ Cross vs 30 Double Zone".

## Players during plays and `/i`

Without `/i` the game still switches to the CGA field for every play and
draws the players (its graphics option is on by default), so the
Modern view shows the 22 moving discs for every play by default. `/i` only
adds the instant replay after the play (the view shows it the same way), so
the project does not need to pass it by default. Two settings make the game
skip the graphics: its "Exit graphics mode" toggle, and extra-extra-fast
mode (Alt+E),
in which the game does not compute play positions at all. Then the view
shows the field overview with the moving ball marker and the play text,
which is all the game has.

## Verification in Godot 4.3

With the official Godot 4.3 Linux binary (`build/godot/`, fetched and
checked by `tools/godot_check.sh`), under `xvfb-run` with Mesa llvmpipe
(OpenGL 3, software rendering), at 1920x1080:

```sh
xvfb-run -a godot --path godot --rendering-driver opengl3 -- --dos-root=$G --args=/i \
    --view=modern --field=horizontal --keys-file=script.k --shot-dir=$PWD/build/godot/shots \
    --quit-when-done [--view-trace=trace.txt]
```

`--keys=`/`--keys-file=` drive the interactive program with the key-script
language of `host/key_script.gd` (blocking after each `{shot:}`/`{click:}`
until the main thread has done it); `{shot:NAME}` saves
`get_viewport().get_texture().get_image()` three frames after the request;
`{click:ACTION}` finds the Modern view's button with that action and clicks
its centre through `Input.parse_input_event` (motion, press, release), so the
real GUI input path, hotspots and key injection are exercised;
`{click:hostkey:KP_2}` sends a numeric-keypad key event. Three scripts cover
setup to the first play and its replay (horizontal and vertical, full field
and zoom, toggled with the toolbar buttons), the half-time statistics and
the PgDn pages (paged with the PgDn button, the game run at turbo with the
Speed button), and a human coach on the right keypad (kick menu, defence
group and play chosen with a button click, a real keypad key, and a button
click; the game's play text then named the defence chosen, entry 3 "20
Three Deep").
Screenshots: `build/godot/shots/*.png`, the same states as the previews.

Found and fixed in real Godot:

* the right-keypad coach's buttons did nothing (keypad digits need Shift
  with this BIOS, see "Coach keys"); fixed for buttons and the real keypad;
* the ball on the field overview sat a yard off the line of scrimmage (the
  view animated it towards the 2-yard marker column instead of the spot
  `MatchView` uses); it now eases to the same place as the previews;
* the time-out dots were drawn larger than designed (an extra 1-px
  antialiased arc around each circle); now one antialiased `draw_circle`;
* a hover highlight stayed on screen after its window closed under a still
  mouse; the highlight is now re-evaluated every frame;
* after a turnover, the play's players were mirrored around the line of
  scrimmage and labelled with the wrong team: the game sets the possession
  variable (`57EC`) to the play's result before showing the play;
  `GameState` now takes the team with the ball from the text scoreboard
  shown before the play (`play_poss`, latched for the play and its replay);
* the zoom camera rasterised the whole field SVG again on every frame while
  it eased (a new texture per pixel size, up to 600 cached full-field
  textures): large art is now rasterised at scales rounded to steps of
  2^(1/8), capped at 4096 px, with a 64-Mpixel cache budget (71-118 SVG
  rasterisations per run in total);
* discs whose declutter offsets crossed flipped sides between frames (jumps
  of 20-37 px at zoom); in the live view the offsets now ease at 10/s
  (`MatchView.DECLUTTER_RATE`, previews and checks unchanged).

Measured (llvmpipe, 1080p, 4 CPU cores shared with the program thread):
28-31 frames per second (mean 33-35 ms per frame; 22 fps while other test
jobs shared the CPU), worst 150 ms;
the Modern view itself takes 1.6-1.9 ms per frame to read memory and lay out
and 0.8-1.1 ms to issue its draw calls, so the frame time is the software
renderer's. A `--view-trace` of a zoomed play shows the camera easing
without reversals except at the replay cut and at the first frames of the
play (players placed one by one). Text, rotated text (end zones, yard
numbers), StyleBox corners, alpha (the compact pill) and SVG art match the
previews. Godot logs no script errors; the only warnings are the sandbox's
(no V-Sync, no ALSA device) and, at exit, "ObjectDB instances leaked" / "30
resources still in use": the program's host objects (Machine, BIOS, DOS,
RecompHost and the generated units reference each other), none from
`ui/`.

## Verification without Godot

Before Godot could run here, and still as the deterministic tests: everything below runs the **shipped GDScript**:
`tools/gdport/gd2py.py --ui` translates `godot/ui/*.gd` (except
`modern_view.gd`, which only draws) to Python; `--ui` makes `/` Python's true
division and adds the String/Array methods the UI code uses, and leaves the
translation of the recompiled program unchanged.

```sh
G=/path/to/nfl
python3 -I tools/modern/capture.py --game-dir $G            # real states -> build/modern/states/
python3 -I tools/modern/render.py                           # SVG + PNG -> build/shots/modern/
python3 -I tools/modern/render.py --field vertical [state ...]   # vertical field, *_v.png
python3 -I tools/modern/check_positions.py --game-dir $G --ticks 12000 --fast
python3 -I tools/recomp/lockstep.py --game-dir $G --with-ui season
python3 -I tools/recomp/lockstep.py --game-dir $G --with-ui boot
python3 -I tools/modern/research.py --game-dir $G scoreboard|records|scroll
(cd godot && gdparse main.gd ui/*.gd && gdlint main.gd text_screen.gd ui/*.gd host/*.gd)
```

* **Previews.** `capture.py` runs the recompiled program (the generated
  GDScript and the host layer, translated to Python, deterministic clock)
  through scripted games and saves the BIOS data area, the video memory and
  DGROUP at chosen moments. `render.py` feeds each state to `GameState`
  (after the text state just before it, as the app would have seen it),
  `Screens.build()` lays out the frame, `DrawList.to_svg()` writes it, and
  `rasterize.js` renders it with headless Chromium (Playwright) at 1920x1080
  with the bundled fonts. Text widths are measured with FreeType on the same
  font files, so the layout matches. The SVG is a mirror of what the Godot
  canvas draws from the same DrawList, not a separate renderer.
* **Positions.** `check_positions.py` runs games with plays and instant
  replays and checks every graphics frame: (1) each player's field position
  from `GameState`, mapped back to CGA pixels, must match an O or X sprite
  that the game drew there (recognised in the CGA frame with the sprite shapes
  from data file A); sprites are XOR-drawn, so where two overlap neither shape
  survives and only isolated players are a fair test; (2) every yard line the
  game drew must fall on a multiple of 5 yards of the view's scale; (3) at
  each snap the ball must be within a yard of the line of scrimmage the text
  scoreboard showed before the play.

  The host's deterministic clock now also ticks on loop back-edges, so a
  tick can fall while the game is still writing records or redrawing its
  XOR sprites. The pixel and yard-line comparisons therefore use settled
  frames only (records and CGA memory unchanged since the previous tick).
  Scrolling is modelled as the game does it (ovl 5 `196D:0DFE`): it first
  moves every record by the scroll step (`196D:0F91`, `1AAD:0050`), then
  updates `8755` and copies video memory along the field, then updates
  `8753` and copies it sideways column by column (ovl 7 `1CE3:0008`).
  Records and scroll variables therefore always agree, and `GameState` uses
  them as they are; only the screen lags while the copy runs. The copy is
  about 16,000 loop iterations, so in this test clock (a tick every 64
  polls, loop back-edges included) it spans up to ~250 ticks, at real speed
  a moment. While the scroll routine is on the program's call stack (the
  harness runs the tick hook on that stack), a sprite counts as matched at
  its record or exactly where the field point was under the scroll from
  before the routine started (both axes, or sideways only once the copy
  along the field is done); those are reported separately. Records the
  game has not written yet in the current graphics session are not shown by
  the view and not checked (`placed`, see "Players"); the ball spot is
  checked at the first frame with the whole line-up written.

  Result (`--ticks 12000 --fast`; the slower clock reaches one play and its
  instant replay at normal speed, the Alt-E game draws no players): 2,367
  graphics frames, 1,542 settled; 18,303 isolated player positions, **all
  at exactly the pixel where the game drew the sprite**; 1,101 stale records
  hidden while the game computed the line-up; 0 of 1,541 settled frames with a
  yard line off a 5-yard multiple; 1 of 1 snaps within a yard of the
  scoreboard's line of scrimmage. Visibility of the discs in the view's own
  layout, in all four layouts (horizontal or vertical, full field or the
  auto-framing zoom, the camera eased per tick and contained as in the
  app): **0 off-screen across the width, 0 along the length, 0 under the
  scoreboard pill**. The declutter moved a disc by at most 0.45 yd (its
  cap). PASS.

  A longer normal-speed run (`--ticks 36000`, three plays with instant
  replays, one a long pass with the field scrolling sideways and along)
  gives 19,358 graphics frames, 11,978 settled: **0 off-screen and 0 under
  the pill in all four layouts** (23 disc-frames of one player 6 yards
  beyond the far sideline were pinned to the edge in the horizontal full
  field), 0 of 11,973 frames with a yard line off a 5-yard multiple, 3 of
  3 snaps within a yard, 3,372 stale records hidden. Of 79,194 isolated
  positions **78,869 (99.59 %) are at the exact pixel**: 69,154 at their
  record, 9,715 during the 5,155 frames in which the game was still copying
  video memory for a scroll, at the pre-scroll place of the same field
  point. Of the remaining 325, the listed ones are one record at the left
  screen edge (x = 6) with no sprite drawn there. PASS.
* **Passivity.** `lockstep.py --with-ui` runs `GameState.update()` and
  `Screens.build()` on every BIOS tick of the recompiled side (as main.gd does
  every frame), cycling through both field orientations with and without
  the zoom camera (eased and contained as in `modern_view.gd`), while
  comparing it with the oracle at every service call and memory checkpoint.
  `boot` passes with the layer active: 16,496 service calls, 4,904 CGA
  frames identical, `CONFIG.NFL` identical; the layer ran on 14,692 ticks,
  3,639 of them play frames, 1,821 with the zoom camera framing a play.
* **Human coach.** `capture.py humanplay` plays a human coach through the
  Modern view's own key injection and checks that the called play runs
  (9/9 states, "YZ Cross" called, "YZ Cross vs 30 Double Zone" run).

## Limitations

* Run in Godot 4.3 only under Xvfb with software OpenGL (llvmpipe): no real
  GPU, no audio device (Godot's dummy driver), no person at a real mouse and
  keyboard (clicks and keys went through `Input.parse_input_event`), and
  frame times are those of software rendering. SVG features were kept to
  what ThorVG (Godot's rasteriser) supports: rect, circle, ellipse, path,
  line, groups, opacity, stroke-dasharray; no text, no gradients, no ids.
* The Python previews are rendered by Chromium from the same draw list;
  Godot's text and antialiasing differ by a pixel here and there (thin SVG
  lines such as the line of scrimmage look slightly heavier in Godot).
* In extra-extra-fast mode (Alt+E) or with the game's graphics turned off,
  the game computes no player positions; the Modern view then shows the
  moving ball marker and the play text, not players (see "Players during
  plays and `/i`").
* Positions come from the play records as the game draws them; when sprites
  overlap the view separates them by drawing order (defence under offence,
  carrier on top).
* Team colours are a small built-in table keyed by the team name the game
  prints; other team disks get neutral home blue / away red.
* Numbered entries of play-calling windows send the coach's keys
  (F1-F10 or the keypad, "Coach keys"); other numbered options (the
  configuration) send the digit. Windows of a computer coach get the same
  buttons; the game ignores them there.
* The save-game and exit screens were not reached in scripted runs and use
  the text fallback.
* While the game copies video memory for a scroll (a moment at real speed)
  its screen still shows the pre-scroll picture; the Modern view shows the
  game's positions (records and scroll variables, which are updated first).

### Godot 4.3 API audit

Checked by reading every call in `godot/ui/*.gd` and the Modern-view parts
of `main.gd` against the 4.3 API, since the transpiled tests cannot catch
what only Godot's analyser or renderer would reject:

* Signatures in use: `Image.load_svg_from_string(svg, scale) -> Error`,
  `ImageTexture.create_from_image(img)`, `draw_string(font, pos, text,
  alignment, width, font_size, modulate)`, `Font.get_string_size(text,
  alignment, width, font_size)`, `draw_set_transform(pos, rot, scale)`
  (reset to identity after each rotated item), `draw_circle(pos, r, col)`,
  `draw_arc(c, r, a0, a1, points, col, width, aa)`, `draw_line(..., width,
  aa)`, `draw_colored_polygon`, `draw_texture_rect(tex, rect, tile)`,
  `StyleBoxFlat.draw(canvas_item_rid, rect)` with `set_corner_radius_all`,
  `set_border_width_all`, `corner_detail`, `anti_aliasing`;
  `ConfigFile.load/save -> Error` (checked), `FileAccess.get_file_as_string`,
  `JSON.parse_string`, `FontFile.load_dynamic_font`, `Color.html` (all
  colours are `#rrggbb` or `#rrggbbaa`), `String.contains/get_slice/
  trim_prefix/trim_suffix/xml_escape/pad_zeros`, `Rect2.get_center`,
  `OS.find_keycode_from_string`, `KEY_PRINT`, `KEY_KP_*`. All match 4.3.
* Static typing: a `:=` declaration whose value is a Variant (an element of
  an untyped Array, arithmetic on one, a ternary with a Variant branch) is a
  compile error in Godot 4 although gd2py accepts it. A scan of all `:=`
  lines found two: `MatchView.frame_camera()` (`hi[0] - lo[0]` on untyped
  arrays) and `_wrap_join()` (a ternary on a loop variable); both now
  declare `: float` / `: String`. No typed arrays, `@onready` or `@export`
  are used; static functions use no members; no Python-isms (`len()`,
  slices, `str * n`, `.items()`, `%` on floats); every `/` has a float
  operand, so true division in the transpiled tests equals Godot's.
* `_draw()` only reads the DrawList; `refresh()` builds it and calls
  `queue_redraw()`.
* `.import` files: `[remap]` / `importer="keep"` is the 4.x format for
  "Keep File (exported as is)": the SVGs stay plain files that
  `FileAccess` can read in the editor and in exports; `state_map.json` is
  exported as a JSON resource file.

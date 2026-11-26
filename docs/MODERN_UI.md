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
that sends the R scancode, exactly like the key).

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
  camera. A display-only declutter (6 relaxation passes) pushes overlapping
  discs apart, at most half a disc radius (0.40 / 0.45 yd), so numbers at
  the line stay readable; drawing order is defence, offence, ball carrier.
  The game's positions are not changed (the position check uses them).
* **Team colours** (`UiTheme.kit_colors`). The home team keeps its primary
  colour. If the visitor's primary is closer than 18 (OKLab distance x100),
  the visitor switches to its secondary colour; if that clashes too, to the
  neutral kit (white, near-black, red, amber) farthest from the home colour.
  The result is used everywhere: end zones, scoreboard blocks, discs, chips,
  match-up cards and statistics pills. Text on a colour is white or
  near-black by WCAG contrast; on a white kit it is the team colour when that
  has at least 4.5:1 contrast. Discs move smoothly: the game updates
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
  Zoom (a camera option for plays that follows the ball at 1.8x; the default
  is the full field), More (shows the HD/Classic toolbar with Reset, Game
  folder and Colors), Fullscreen.
* **Info strip** (dark, bottom): the team with the ball and its time-outs,
  the play call and result text the game prints under its field ("HB Option
  Weak vs 30 Double Zone", "Run... Gained 1 yard. Carried by #20, RB ..."),
  and during plays the offence's 11 players as chips (number and position).
* **Screens.** Configuration (a card with option rows), team selection (AFC
  and NFC rows of division cards, the game's cursor highlighted), match setup
  (two team cards with coach and keypad, the prompt with key buttons, the coin
  toss), title and loading, the field overview with the game's windows as side
  panels (play lists, substitution groups, coach menu, kick and penalty menus;
  numbered entries become buttons), roster tables, notices under the field
  (injuries, with the game's block-letter caption as a badge), y/n questions
  as Yes/No buttons, and team statistics (section cards with comparison
  bars, PgUp/PgDn/Continue buttons).

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

Driven through the recompiled program in four scripted runs (CPU vs CPU
with instant replay, Colts at Bills and Dolphins at Bills; a human coach on
the right keypad; a whole game at extra-extra-fast speed to the "Another
Game" question):

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
| "Another Game (y/n)?", "Do you wish to pick new teams (y/n)?" | Yes/No buttons | `43` |
| Anything else (additional statistics pages, save-game and option menus, messages not seen in the runs) | the original text (HD renderer) inside a modern frame | - |

Not reached in the scripted runs and therefore only covered by the fallback:
the additional statistics pages (PgDn), the save-game and exit menus, the
options menus of overlay 4, penalty and fourth-down menus (they are windows
over the field, so they get side panels when they appear), and the human
coach's play selection: the keypad mapping of the human coach was not found,
so the scripted human game stops at the coach menu.

## Verification without Godot

Godot could not be run here. Everything below runs the **shipped GDScript**:
`tools/gdport/gd2py.py --ui` translates `godot/ui/*.gd` (except
`modern_view.gd`, which only draws) to Python; `--ui` makes `/` Python's true
division and adds the String/Array methods the UI code uses, and leaves the
translation of the recompiled program unchanged.

```sh
G=/path/to/nfl
python3 -I tools/modern/capture.py --game-dir $G            # real states -> build/modern/states/
python3 -I tools/modern/render.py                           # SVG + PNG -> build/shots/modern/
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

  Result (`--ticks 12000 --fast`: a normal-speed CPU game with instant replay
  and an extra-extra-fast one, 5,654 graphics frames, plays and replays):
  62,529 isolated player positions, **all 62,529 at exactly the pixel where
  the game drew the sprite**; 0 of 5,654 frames with a yard line off a 5-yard
  multiple; 9 of 9 snaps within a yard of the scoreboard's line of
  scrimmage. Counting overlapping sprites too, 79.5 % of 111,255 positions
  match a recognisable sprite within 2 px (the rest are shapes destroyed by
  XOR overlap). The display-only declutter moved a disc by at most 0.45 yd
  (its cap). PASS.
* **Passivity.** `lockstep.py --with-ui` runs `GameState.update()` and
  `Screens.build()` on every BIOS tick of the recompiled side (as main.gd does
  every frame) while comparing it with the oracle at every service call and
  memory checkpoint. Both `season` (728,068 service calls, `STATS.LOG`
  byte-identical, the layer ran on 11,587 ticks) and `boot` (541,803 service
  calls, 4,137 CGA frames identical, the layer ran on 8,524 ticks including
  4,060 play frames) pass with the layer active.

## Limitations

* Not run in Godot: `modern_view.gd` (texture rasterisation, drawing, input)
  and the `main.gd` integration are checked only by `gdparse`/`gdlint`. SVG
  features were kept to what ThorVG (Godot's rasteriser) supports: rect,
  circle, ellipse, path, line, groups, opacity, stroke-dasharray; no text, no
  gradients, no ids.
* The previews are rendered by Chromium from the same draw list; Godot's font
  rendering and antialiasing will differ slightly.
* Without `/i` the game shows plays only on the text field (the ball marker
  moves); the Modern view then shows the moving ball, not players, because the
  game does not compute player positions it does not draw.
* Positions come from the play records as the game draws them; when sprites
  overlap the view separates them by drawing order (defence under offence,
  carrier on top).
* Team colours are a small built-in table keyed by the team name the game
  prints; other team disks get neutral home blue / away red.
* Numbered buttons send the digit key. That is what the configuration and
  the computer coaches' screens accept; the human coach's menus did not react
  to the digit keys (nor to the keypad keys tried) in the scripted run, so
  their keypad mapping is still unknown and those buttons may do nothing.

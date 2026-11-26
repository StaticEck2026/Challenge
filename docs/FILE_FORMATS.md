# NFL Challenge data file formats

This document is reverse engineered from the files themselves and from `NFL.EXE` and
`SEASON.COM` (see `docs/NFL_EXE_ANALYSIS.md` for the address conventions: `SSSS:OOOO` =
NFL.EXE link address, `DGROUP:xxxx` = data segment offset). **Confirmed** means read from
the parser or writer code or checked against every shipped file. **Guess** marks inference.

Tools: `tools/disasm/dump_team.py` (team rosters, with the game's own range checks) and
`tools/disasm/dump_window.py` (the A–Q window files).

## 0. Who opens what, and from where

`config.nfl` gives a team-disk prefix, `"x:"`, which is kept at DGROUP:514A. Team-disk
names are built by `12CA:0064` (`strcat("x:", name)`). Files named without a prefix are
opened relative to the current directory, which is the program disk.

| File | Opened by | Where | Mode | Notes |
|---|---|---|---|---|
| `nfl.EXE` | overlay manager `191F:00BE` | cwd, then each `PATH=` dir | DOS AH=3Dh AL=0 | overlays read on demand |
| `config.nfl` | ovl 11 `196D:300F` (read), `196D:3AB2` (write) | cwd | `"r"` / `"w"` | pointer at DGROUP:4FDD |
| `league.nfl` | ovl 11 `196D:3C8D` | cwd | `"r"` | `12CA:03D7` "Can't Open %s" on failure |
| `A` | ovl 7 `1C3B:0350` (`fopen("a","rb")`) | cwd | `"rb"` | CGA sprites |
| `B`,`C`,`D`,`E`,`F` | ovl 4 `196D:0000`: name buffer `"B"` at DGROUP:15AF, `inc byte` after each of 5 loads | cwd | via `130A:0AB0` (`"rb"`) | text windows |
| `G`,`H`,`I` | root `0AAB:0021/0043/0065` (names `"g"`,`"h"`,`"i"`) | cwd | `130A:0AB0` | windows |
| `J` | ovl 8 `1E02:000B` → `1E02:00DE` | cwd | `130A:0AB0` | window |
| `K` | ovl 5 `196D:1328` | cwd | `"rb"` | CGA sprite sheet |
| `L` | ovl 7 `1B69:0D1A` → `1D0C:0002("L",5,…)` | cwd | `"rb"` (DGROUP:3BF4) | replay tables (guess) |
| `M` | ovl 11 `196D:4063` | cwd | `"rb"` | team-selection screen |
| `N` | ovl 8 `1A09:0ED9` (`"n"`) | cwd | `130A:0AB0` | window |
| `O`,`P`,`Q` | ovl 11 `196D:5A5A/5A78/5A96` | cwd | `130A:0AB0` | coin-toss pictures |
| `<team>.nfl` | `12CA:008A` (`"x:" + DGROUP:83CF or 8400 + ".nfl"`), parsed in ovl 1 | team disk | `"r"` | rosters |
| `pbp.nfl` | ovl 2 `203D:0023` | team disk | `"rb"` (DGROUP:0ECE) | |
| `plyb.nfl` | ovl 2 `208E:0028` | team disk | `"rb"` | |
| `pls.nfl` | ovl 2 `20C2:004D` (pointer DGROUP:4588) | team disk | `"r"` | |
| `curves.nfl` | ovl 2 `2149:001A` | team disk | `"r"` (DGROUP:0F85) | |
| `Season.nfl` | ovl 11 `196D:0839` | team disk | `"r"` | only with `/S` |
| `stats.log`, `avgstats.log` | ovl 10 `2091:0006/0012` | team disk (per SEASON.COM help text) | `"a"` (append) | |
| `game.N` | `12CA:00FF` (`"x:" + "game." + "%-d"`) | team disk | | saved games; deleted with AH=41h |

`*.ROS`, `READ.ME2`, `FORMFEED.CHR`: never opened by NFL.EXE (no such strings exist).

All text files use CP437 text with CRLF line ends. The shipped ones end with `^Z` (1Ah).

## 1. `<TEAM>.NFL`: team roster (text, **confirmed**)

Example (`89/BEARS.NFL`, abridged):

```
                             1989 Chicago Bears
  No.  Pos   Ht.   Wt. Yrs. Spd  Run  Rcv  Blk  Pass Kick    Players: 47
-----------------------------------------------------------------------------
   4   QB   6' 3"  204   3  4.6  81   50   50   87   50   Jim Harbaugh
   ...
   64  OL   6' 4"  274   1  5.0  50   50   82   50   50   FILLER PLAYER

STARTERS
LT    74
...
10¢   43
K      6
P      8
PR    31
KR    29
Offense:
Conservative
Defense:
Conservative
^Z
```

- Line 1 is the title. Line 2 is a column header ending in `Players: N`. Line 3 is a rule.
- **Player lines** are parsed with the scanf format at DGROUP:0BFC,
  `"%d %s %d'%d\" %d %d %d.%d %d %d %d %d %d %s %s %s %s"` (ovl 1 `196D:066C`).
  The fields are: jersey, position, height feet, inches, weight, years, speed (whole and tenths),
  Run, Rcv (receive), Blk (block), Pass, Kick, then a name of up to 4 words.
  The loader rejects values outside these ranges (`196D:0683`-`07D9`): jersey 0–100, feet 5–7,
  inches 0–11, weight 100–400, years 1–30, speed 4.0–7.9, every rating 50–99.
- Position codes used: `QB K P RB WR TE OL C DL LB DB`. These names are also in
  DGROUP:090C.
- `FILLER PLAYER` lines pad the roster. Every one of the 84 shipped team files has
  **exactly 49 player lines**, and `Players: N` equals the number of non-filler lines
  (44–49). **Guess:** 49 is the fixed team size that the `Wrong team size %d or Can't
  find STARTERS` check (DGROUP:0BD2) enforces.
- **STARTERS**: exactly 32 `LABEL jersey` pairs, read by a loop of 32 `fscanf("%s %d")`
  calls (`196D:00D7`-`0219`). Each label is looked up by `115C:0A2F`. Errors are
  `Bad starter label %s`, `Starter %d not found` and `Not enough starters`.
  Labels in the shipped files are:
  - offense: `LT LG RG RT C TE TE2 TE3 FL SE WR3 FB HB QB`
  - defense: `LE LDT|L/NT R/NT|RDT RE LOLB LILB|LMLB RMLB|RILB ROLB LCB RCB SS FS 5¢ 10¢`
  - special teams: `K P PR KR`

  `5¢` and `10¢` (CP437 9Bh = ¢) are the nickel and dime backs.
- **Persona**: `Offense:` / `Defense:`, each followed by `Conservative`, `Balanced` or
  `Aggressive`.
- `dump_team.py --check` passes on all 84 files (28 teams × `84/`, `89/`, `Greats/`).
- The disks differ only in content. `Greats/` titles read e.g.
  `Chicago Bears : 1920 - 1989 All Stars`, and `84/` titles `1984 Chicago Bears`.
- File base names are the short team names from `LEAGUE.NFL` (`Bills`, `49ers`, ...),
  and the game appends `.nfl` itself (DGROUP:0A44).

## 2. `<TEAM>.ROS`: printable roster (text, **confirmed not read by NFL.EXE**)

`89/READ.ME2`: "We have included printable team rosters which include player names ...
`for %a in (*.ros) do copy %a + formfeed.chr prn`". The content is the same team in a
human layout:
- a `STARTING OFFENSE (persona)` block, then defense, specials and reserves;
- columns `No, Player Name, Pos, Ht, Wt, Yrs, Spd, Run, Rcv, Blk, Pass, Kick`;
- position labels are the starter labels (TE, SE, FL, LT, ...).

`FORMFEED.CHR` is a single 0Ch byte.

## 3. `LEAGUE.NFL` (text, **confirmed**)

```
28
Buffalo Bills        <- display name
Bills                <- file base name (Bills.nfl)
...                  (28 pairs)
5 4 5 5 5 4          <- teams per division
```

The division order is AFC East/Central/West, then NFC East/Central/West, which is the
order of the boxes in `M`. NFL.EXE reads the last line with
`"%d %d %d %d %d %d"` (DGROUP:3A5D). A 12-integer variant
`"%d %d %d %d %d %d %d %d %d %d %d %d"` (DGROUP:3A6F) also exists, use not traced.

## 4. `config.nfl` (text, **confirmed**)

Read with `fscanf(f, "%s %d %d %d", DGROUP:514A, &DGROUP:515E, &DGROUP:5160, &DGROUP:5162)`
(ovl 11 `196D:3080`-`30C6`), with the same format to write. The screen shows it as
`Drive Color Delay Special`.

| Field | Var | Meaning |
|---|---|---|
| `%s` | 514A | team-disk drive prefix, `a:`…`e:`. It is the start of every team-disk path. **Without a `config.nfl` the default is `b:`** (`196D:3033`-`3053` writes `'b'`, `':'`, NUL). The CONFIGURATION screen overwrites the letter with `a`–`e` (`196D:3311`-`3361`). |
| `%d` | 515E | display type. Menu entry 1..6 is stored as 3, 0, 5, 4, 1, 2 (`196D:36FA`-`3753`). Without a config file it is 0 if INT 10h AH=0F returns 7, else 1. Types 0/3/4/5/6 get monochrome-safe attributes (`130A:0005`). |
| `%d` | 5160 | delay factor, default 100 (= "same speed as PC"). Busy loops use `delay/100`. |
| `%d` | 5162 | "special": non-zero makes `12CA:0342` alternate `Place Program Disk in drive X` / `Place Team Disk in drive X` prompts, which is a single-drive disk-swap mode (meaning inferred from that code). |

A file suitable for a hard disk with the team files in `C:\NFL\89` would be, e.g.,
`c: 1 100 0` with the current directory set so that `c:` resolves to the team directory.
This value is a **guess**, not yet tested in the emulator.

## 5. `season.nfl` (text, **confirmed from both writer and reader**)

Writer: `SEASON.COM` (Turbo Pascal 3 COM, "Season File Manager Version 1.0",
© 1988 XOR). At COM address `4B6E`-`4C4F` it does:
- `Assign(f, chr(drive) + ':season.nfl'); Rewrite(f)`
- `WriteLn(f, n)`, with n = `[16AA]` (at most 50 games per the help text)
- `for i := 1 to n do WriteLn(f, short[g[i].a], ' ', short[g[i].b])`

`g` is an array of word pairs at `15DE`. `short[k]` is the 13-byte `string[12]` at
`2D96 + 34*k`, the second half of each 34-byte team record whose first half is the
`string[20]` full name. The first team picked fills `.a` (`40C7`), the second `.b`
(`40DC`).

```
3
Bears Packers
Vikings Lions
49ers Rams
```

Reader: NFL.EXE ovl 11 opens `x:Season.nfl` (`196D:0839`), reads the count with `"%d"`
(DGROUP:3633), and per game runs `sscanf(line, "%s %s", DGROUP:83CF, DGROUP:8400)`
(`196D:4E6F`). It then loads `x:<DGROUP:83CF>.nfl` and `x:<DGROUP:8400>.nfl` via
`12CA:008A(1)` / `(2)`.

Which token is home: SEASON.COM prompts `Choose the HOME team` first, then `Choose the
VISITING team`, so the first token is **home, second visitor (strong inference from the
prompt order; not confirmed in NFL.EXE)**. On a parse failure the game prints `Bad
Season.nfl file`.

SEASON.COM also keeps its own `config.sea` (team-disk drive letter).

## 6. Saved games `game.N` and logs (partially)

- `x:game.N` (`12CA:00FF`, N from `/Rn` or the restore screen). Text sections are
  labelled by the strings in DGROUP:0A53 ff.:
  - `Coach, Keypad, Team (Home and Visitor); Coin toss results` with `%d %d %s %s` lines
  - `Quarter and time`
  - `Visitor/Home score by periods & timeouts remaining`
  - `sit, play, tm w/ball, dir, ydline, strt yd, down`
  - team and individual statistics, `persona variables`, `League Version Data`, ...

  Layout beyond these labels was not traced.
- `stats.log` / `avgstats.log`: appended reports built from the format strings at
  DGROUP:2A64-2Cxx (`NFL Challenge Simulation Version %s`, `Results from Game %d`, box score
  `----1----2----3----4----Total---`, team and individual stats). They are output only.

## 7. Play data on the team disks (identical on 84/89/Greats)

`PBP.NFL`, `PLYB.NFL`, `PLS.NFL` and `CURVES.NFL` have the same MD5 on all three team disks.

### `CURVES.NFL` (text, structure **confirmed**, semantics from the titles)

A sequence of tables, each laid out as:

```
<title line>                       e.g. "Running curve", "Field Goal", "Punt return",
<n> <x>                            n = number of rows; x = a per-table parameter
<value> <cumulative>               n rows; cumulative ends at 10001 (probabilities out of 10000)
999 check                          terminator
```

There are 21 tables: Injury curve by position, Running, Passing, Block time, Scramble
Run, Punt, Punt return, Blocked punts, Field Goal, Kickoff, Kick Return Curve1–3,
Puntcc (coffin corner) yardline curve1–2, Puntcc Return, Kick onside, Kick squib,
Interception runback3/4. They are read with `%d %d` pairs (DGROUP:0F87, 0F8D, 0F96). The header
says `revision date: 04/03/86`.

### `PLS.NFL` (text, structure **confirmed** from its own header)

```
Play List file.  Format: Window #, # entries, assd window,color
    for each entry, the id of the play and the rg number   Rev. Date:  04/03/86
10 5 1 7
    46 6   47 7   48 7   49 7   50 7
...
Offense: 45
Play-id  Option  Attack  BC/M2M   A-bltz  Prtr  Recv
026      000      1       8       0       0      0
...
Defense: 26
Play-id  Option  Defend  M2M-Zn   Bltz   Subgp  Rs-Rd
101      101      2       07      0      11     43
```

The first part is play-selection window definitions: window number, entry count,
associated window, colour, then `(play id, rg)` pairs. `Bad rg %d` is at DGROUP:0F49. Then
come per-play coaching attributes for 45 offensive and 26 defensive plays, read with
`"%d %d %d %d %d %d %d"` (DGROUP:0F5B).

### `PLYB.NFL` (binary playbook, partly understood)

- Byte 0 = 97 (61h). **Guess:** the number of plays. `PBP.NFL` also starts with 61h.
- Each record has this header: `id, kind, b2, b3, link, len`, then a `len`-byte name,
  then about 53 bytes of assignment data (records are 66–75 bytes long).
- In offensive plays `kind` is 1 for runs and 2 for passes, and `b2 = b3 = 0`.
  Defensive plays (ids ≥ 101) have `b2 = 1` and appear twice, with `kind` 1 and 2
  (**guess:** the variant used against run and against pass). Examples:
  - `06 01 00 00 05 0E "HB Dive Strong"`
  - `0C 02 00 00 0B 0C "SE Loop Pass"`
  - `7D 01 01 06 31 11 "4-3 Ovr Blitz Man"`, then `7D 02 01 06 30 11` with the same name
- The play ids match the `Play-id` column of `PLS.NFL`. The error `Can't find play-id`
  is at DGROUP:0F0F.

### `PBP.NFL` (binary, partly understood)

- Byte 0 = 97, then records that start with 11 signed `(x,y)` byte pairs, e.g.
  `F8 EC  F8 F6  F8 00  F8 0A  F8 14  F8 1E  F8 E2  F0 28  D8 00  E0 EC  F0 00`.
  These are 11 players at line depth −8 (the line), −16, −32 and −40 (backs), spread
  laterally, which is a **formation**.
- Movement data follows (bytes like `05 28 31 31 31 31 F9 02 FF`, **guess**: per-player
  paths).
- The load errors next to `pbp.nfl` (`Too many def. forms`, `Too many off. forms`,
  DGROUP:0ED2/0EE7) confirm that it holds offensive and defensive formations.
  "pbp" here is the on-screen play diagram, not the text play-by-play.
- The text play-by-play messages (`Run...`, `Tackled by #%s,` ...) are string constants
  inside NFL.EXE (DGROUP:01DA ff.).

## 8. Program data files `A`–`Q` (cwd, binary)

Most of these files are padded with zeros to a multiple of 128 bytes.

### Text windows: `B C D E F G H I J N O P Q` (**confirmed** by rendering all of them)

```
byte rows, byte cols, rows*cols × (char, attribute), row-major
```

The loader is `130A:0AB0(name, &rows, &cols)`. It returns a far pointer and is used by
every caller listed in §0.

| File | Size | rows×cols | Content |
|---|---|---|---|
| B | 128 | 3×16 | `1 Normal kick / 2 Onside kick / 3 Squib kick` |
| C | 242 | 6×20 | substitution groups White/Blue/Purple/Red/Yellow/Special |
| D | 162 | 4×20 | `Short yardage / 3-4 Normal / 4-3 Normal / Long yardage` |
| E | 128 | 3×18 | `Go for it / Punt / Field goal` |
| F | 202 | 5×20 | `Punt / Field Goal / QB Down Ball / Throw Ball Away / Elective Safety` |
| G | 1832 | 15×61 | text-mode football field with yard lines |
| H, I | 128 | 5×4 | small bracket/arrow pieces |
| J | 3456 | 21×80 | `TEAM STATISTICS` screen frame |
| N | 130 | 4×16 | `Penalty: 1 to accept / 2 to decline` |
| O, P, Q | 384 | 9×21 | coin-toss art: heads, tails, coin edge-on |

### `M`: team-selection screen (**confirmed**)

12 bytes form six `(row, col)` pairs `(2,12) (10,12) (17,12) (2,40) (10,40) (18,40)`,
which are the top-left corners of the six division lists. Then come `23, 80` and 23×80
(char, attr) cells showing the AFC/NFC East/Central/West boxes. The team names are baked
into the picture. 3712 bytes = 14 + 3680, padded.

### `A`, `K`: CGA mode-4 sprites (**structure confirmed, use is a guess**)

These are a sequence of `byte w (bytes per row), byte h (rows), w*h bytes` of 2-bpp
packed pixels (4 pixels per byte).
- `A`: 3 sprites of 3×9 bytes (12×9 px) at offsets 0, 29 and 58, then zero padding to 128.
- `K` (14720 bytes): many sprites, e.g. `5 17` (20×17 px) at 0 and 87, and `4 17`
  further on. **Guess:** player figures for the instant replay. `K` is opened by
  overlay 5, the replay set-up.

### `L` (1024 bytes, **unknown**)

It is read by `1D0C:0002` in overlay 7 (replay) with argument 5. Its content is runs of
small integers (0–13), probably animation or frame tables. Not decoded.

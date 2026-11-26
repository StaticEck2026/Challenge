# NFL.EXE static analysis

Subject: XOR Corporation *NFL Challenge* v2.01 (8/8/89), `NFL.EXE`, 309,264 bytes.

This file records what was established by static analysis. All addresses are
**link addresses**, i.e. segment numbers relative to load segment 0, the way
`tools/disasm/` prints them. At run time add the load segment (PSP + 10h) to
every segment number. "DGROUP:xxxx" is an offset in the data segment
(link segment `23D6`). Every claim below cites the code or data it was read
from. Anything not confirmed is marked **guess**.

Reproduce everything with:

```sh
NFL=/path/to/nfl            # the user's own game directory (never committed)
python3 -I tools/disasm/mz_info.py  $NFL/NFL.EXE --thunks   # header, relocs, overlay table
python3 -I tools/disasm/disasm.py   $NFL/NFL.EXE            # -> build/disasm/{root,ovl01..11}.asm, ovl12.hex
python3 -I tools/disasm/xref.py     $NFL/NFL.EXE            # -> build/disasm/xref.txt
python3 -I tools/disasm/dump_team.py $NFL/89/BEARS.NFL --check
python3 -I tools/disasm/dump_window.py $NFL/B $NFL/G $NFL/M
```

`build/` is gitignored. The listings are copyrighted derivative material, so
never commit them.

---

## 1. Summary for the emulator

| Topic | Finding |
|---|---|
| CPU | **Plain 8086.** Recursive descent reaches 0 bytes of 80186/286 opcodes. The raw-byte hits (`60`, `61`, `C8`, `6x`) all fall in unreached data. One 8087 opcode is reached: `FNINIT` (`DB E3`) at `13F3:0019` in C start-up. All floating point is a **software IEEE-double library** (`13F3:0143`, `0648`, `068E`, ... test `0x7FF0` exponent masks). So decode `D8`-`DF` as ESC (ModRM, no effect) like an 8086 with no coprocessor. |
| INT 83h | **There is none.** All 12 `CD 83` byte pairs in the root (and the 22 in the overlays) are the operand bytes of `[0x83CD]`, a DGROUP variable, for example `8B 06 CD 83 = mov ax,[0x83cd]` at `0031:0B03`. The listings contain 12 root and 22 overlay uses of `0x83cd`, which matches the 12 + 22 byte hits exactly. Overlays are reached through **far-call thunks** into the PLINK86 manager (section 3). |
| How BIOS is called | Almost never with an `INT` opcode. The C library's `int86()` (`13F3:0717`) **simulates the interrupt**: it pushes FLAGS, CS and a return IP, then pushes the IVT vector and executes `IRET` into it. **The emulator's BIOS hooks must fire when execution reaches the handler address in the IVT** (an `F000:xxxx` stub), not on the `INT` opcode. The handler's returned FLAGS matter: `kbhit()` tests ZF from the flags image (`1582:0007`, `and ax,0x40`). |
| DOS | INT 21h AH = 02, 07, 09, 1A, 29, 0F, 27, 2C, 30, 3C/3D, 3E, 3F, 40, 41, 42, 44 (AL=00/01), 45, 4A, 4C, EF. `0F`/`27`/`29`/`1A` appear only on the DOS 1.x path of the overlay manager. `EF` is a Novell probe: leave BX unchanged (=0). No AH=25h/35h: **no interrupt vectors are hooked through DOS.** The overlay manager pokes `0000:0090` (INT 24h) directly while it loads an overlay. |
| BIOS | INT 10h AH=00 (modes 0-3, 4, 6), 01, 02, 03, 05, 06, 07, 09, 0B, 0F; INT 16h AH=00, 01, 02; INT 1Ah AH=00 (random seed). INT 10h AH=02/09 is also called directly by the overlay manager's "insert disk" prompt. |
| Ports | `3DA` in (wait for bit 3, vertical retrace) and `3D8` out (`0x25` = video off, `0x29` = 80x25 text on) around every text-mode screen write (`150F:002D`). PC speaker: `61h` in/out, `43h`=`B6h`, `42h` divisor lo/hi (`15B8`, `15BC`). Nothing else: no keyboard port, no PIC and no CRTC `3D4`/`3D5`. |
| Video memory | Text: `B800` (or `B000` when INT 10h AH=0F returns 7), pages 4 KB apart. Graphics: CGA mode 4 at `B800`, two interleaved 8 KB banks (`14D9` pixel plot, overlay 7 blitters). |
| BIOS data area | Only `0040:001A`/`001C` (keyboard-buffer flush at `158C:0003`: tail := head). So INT 16h should run on the real BDA ring buffer. |
| Files | The overlay manager re-opens `nfl.EXE` by name, from the current directory and then each `PATH=` entry, and seeks/reads it. Program data: `A`-`Q`, `league.nfl`, `config.nfl`, opened from the current directory. Team disk: `x:` prefix from `config.nfl`. **Without a `config.nfl` the prefix defaults to `b:`**, so the emulator should map B: to the team directory, or ship a `config.nfl`. See `docs/FILE_FORMATS.md`. |

---

## 2. MZ header and root image

From `mz_info.py`:

```
e_cblp/e_cp     208/225 -> load image ends at 114896 (0x1C0D0)
e_cparhdr       672 paragraphs = 10752 bytes
root image      104144 bytes = 0x196D paragraphs (file 0x2A00-0x1C0D0)
e_crlc/e_lfarlc 2671 relocations at file offset 28
e_minalloc/max  0x15B4/0xFFFF paragraphs
initial SS:SP   2F11:0100
initial CS:IP   191F:02FB  (linear 0x194EB, file 0x1BEEB)  = PLINK86 manager start-up
appended data   194368 bytes = 12 overlays
```

`minalloc` (0x15B4) plus the image (0x196D) is 0x2F21 paragraphs, which is
exactly the end of the overlay area (overlay 12 ends at `2F21`). That leaves room
for the largest overlay chain and DGROUP. The C start-up then grows the stack
above `2F10` and shrinks the block with AH=4Ah.

Relocations: 2671 entries over 78 fix-up pages, 113 distinct segment values.
83 of the values lie inside the root, 2 are the manager's stack, and the rest point
into the overlay area (`196D`-`23D6`, used by the thunks' `jmp far`) and to DGROUP
`23D6`. The root segment map (`mz_info.py`, "root segment map") has 94
segments. PLINK86 numbers a module's segment as `floor(linear/16)`, so a
module's code may start a few bytes into "its" paragraph, and the previous
function's epilogue may sit in the first paragraph of the next segment number.
`disasm.py` therefore follows near branches inside the 64 KB frame of the
segment it came from, instead of cutting the image at segment starts.

Important root segments (identified by what they contain):

| Link seg | Contents |
|---|---|
| `0000` | `main()` (`0000:0000`, called from `15D7:0364`) |
| `0031`-`12CA` | game code that stays resident (UI, play engine pieces) |
| `12CA` | path helpers: `12CA:0064` team-disk path (`"x:"+name`), `12CA:00FF` saved-game name, `12CA:0342` disk-swap prompt, `12CA:03D7` "Can't Open %s" |
| `130A` | text attribute mapping (`130A:0005`), cursor (`010F`, `018E`), `130A:01DB` = text mode 80x25 colour, `130A:01EE` = CGA graphics, `130A:0AB0` = load window file |
| `13F3` | C runtime: start-up (`0007`), soft-float, `int86` (`0717`), `outp` (`0A14`), `inp` (`0A22`), `bdos` (`0A2D`), `exit` (`0A3C`), `intdos` (`0AA5`), stdio, the inline-table `switch` helpers (`00E7` dense, `07DC` sparse) |
| `14D9`-`15D4` | video/keyboard/sound library (section 6) |
| `15D7` | `_main`: DOS version check, handle set-up, argv parsing, calls `main` |
| `1650` | `fopen(name, mode)` |
| `18B4` | PLINK86 overlay descriptor table + 93 call thunks |
| `1906` | overlay manager data (strings `Fatal error - $`, `Place Program Disk in drive and press any key. $`, `Disk I/O error in $`, `PATH`) |
| `191F` | PLINK86 overlay manager code, entry point `191F:02FB` |

---

## 3. PLINK86 overlay manager

### 3.1 Start-up (`191F:02FB`)

```
191F:02FB  cli / mov di,es / mov si,ss / mov cx,sp
191F:0302  mov dx,18B4 / mov es,dx / mov ss,[es:0002] / mov sp,0100   ; private stack
191F:0317  mov [cs:0286],es                                           ; PSP, for the abort path
191F:0321  call 03CC              ; DOS>=2: find "PATH=" in the environment (PSP:2C)
191F:0329  mov ax,cs / sub ax,[es:0000] / mov [es:0000],ax             ; word0 = 191F (not relocated) -> load base
191F:0334  AH=30h; if DOS>=2: AH=EFh (BX=0 -> open mode 0, else 30h); flag [000A]=FF
191F:035D  for each record: if flags & 4000h -> load it (call 191F:0288 with AH=0)
191F:038A  restore SS:SP and registers, jmp far 13F3:0007        ; C start-up
```

### 3.2 Overlay table (`18B4:0000`, file offset 0x1B540)

Header: word 0 = `191F`, the manager's link CS (not relocated, which is what
makes `CS - word0` give the load base), and word 1 = `2F21`, the manager stack segment
(relocated). Records start at `18B4:0004`, 16 bytes each, and overlay *n* is at
`4 + 16*(n-1)`. The list ends at a record whose flags word is `FFFF`, at
`18B4:00C4`. Field use is taken from `191F:0000`, `01C2` and `0288`:

| Off | Meaning | Evidence |
|---|---|---|
| +0 | flags: bit 15 loaded, bit 14 preload at start-up, bits 0-13 parent overlay number | `or [es:di+4],8000h` after loading (`026E`), `test 4000h` at start-up, `and cx,3FFFh` parent loop (`02B9`) |
| +2 | number of relocation entries | `(n+3)/4` paragraphs skipped (`01E3`), loop count (`0218`) |
| +4 | first load segment (relocated by DOS) | read target (`0207`) |
| +6 | end segment, exclusive (relocated) | overlap test (`0000`), size limit (`01F7`) |
| +8 | offset of the file name in the table segment (`00C6` = `"nfl.EXE"`) | `0068`-`0080` |
| +A/+C | file position in paragraphs (lo, hi) | `00DA`: `(lo:hi + dx) << 4` |
| +E | image size in paragraphs | `01F2` |

The 12 overlays (all in `nfl.EXE`):

```
ovl flags parent pre nrel  load seg     bytes   file off  code off  role (from referenced strings/calls)
  1 0000      0    -  542  196D-203D   27904  0x01C0E0  0x01C960  team roster load (.NFL parser), roster/substitution screens
  2 0001      1    -  118  203D-216D    4864  0x023660  0x023840  loads pbp.nfl, plyb.nfl, pls.nfl, curves.nfl
  3 0001      1    -  272  203D-2219    7616  0x024B40  0x024F80  save game / exit menu ("SAVE GAME", game.N)
  4 0000      0    -  125  196D-1A74    4208  0x026D40  0x026F40  option menus, coach/persona; loads windows B-F
  5 0000      0    -  102  196D-1B69    8128  0x027FB0  0x028150  instant replay set-up: CGA mode 4, palette, sprite file K
  6 0005      5    -   80  1B69-210D   23104  0x02A110  0x02A250  replay animation (guess: from parent 5 and size)
  7 0005      5    -   68  1B69-1D45    7616  0x02FC90  0x02FDA0  replay drawing: direct B800 blits/scroll, files A and L
  8 0000      0    -  991  196D-2091   29248  0x031B60  0x032AE0  play resolution + play-by-play text, penalties, windows N, J
  9 0008      8    -  158  2091-2227    6496  0x039D20  0x039FA0  (statistics helpers, guess)
 10 0008      8    -  431  2091-23D6   13392  0x03B900  0x03BFC0  stats.log / avgstats.log writer
 11 0000      0    -  673  196D-1FA9   25536  0x03F410  0x03FEA0  title, CONFIGURATION, config.nfl, team selection (M, league.nfl), restore, Season.nfl, coin toss (O, P, Q)
 12 4000      0    Y  279  23D6-2F21   20816  0x046260  0x0466C0  DGROUP (initialised data), preloaded at start-up
```

The last overlay ends exactly at end of file. The overlay tree is:
1 → {2, 3}, 5 → {6, 7}, 8 → {9, 10}; 4 and 11 are leaves; 12 is resident data.
Top-level overlays share `196D`, which is the first paragraph after the root image.

On-disk layout of each overlay: at `file off`, `nrel` relocation entries of 4
bytes `(offset, link segment)`. `(0,0)` entries are padding and are skipped. The
table is padded to a paragraph, and `code off` follows it. The relocation
segment is an absolute link segment. The manager applies
`word[(seg+base):off] += base` with `base = [18B4:0000]` (`025B`-`025F`).

### 3.3 Calling an overlay: thunks, not INT 83h

Every root→overlay call goes through one of 93 thunks at `18B4:00CE`-`0529`:

```
18B4:00CE  9A D1 02 1F 19    call far 191F:02D1     ; "ensure overlay loaded"
18B4:00D3  0B 00             dw 11                  ; overlay number (inline data)
18B4:00D5  EA 00 00 6D 19    jmp far 196D:0000      ; the real routine inside overlay 11
```

`191F:02D1` (`pushf`, `push ax bx cx dx bp si di`, `mov bp,sp`) reads the word at the
caller's return address (`[bp+10h]`/`[bp+12h]` = IP/CS), adds 2 to the stacked IP,
and calls `191F:0288` with CX = overlay and AH = 0. It then pops everything, `popf`,
and `retf`s onto the `jmp far`. The return address that the original caller
pushed is left untouched, so the overlay routine returns straight to its caller.
Overlays call into their parent or children directly (relocated far calls).
`mz_info.py --thunks` lists all thunks, and `disasm.py` annotates them.

`191F:0288` (load overlay CX, AH=0 means abort on failure):
`di = (cx-1)*16`. If bit 15 is clear, it first clears the *loaded* bit of every
overlay whose `[start,end)` overlaps (`191F:0000`), then loads it (`01C2`).
It repeats with `cx = parent` until the parent chain is resident. On failure with
AH=0 it executes `jmp far [cs:0284]` = `PSP:0000`, which is `INT 20h`, terminate.

`191F:01C2` (load) does the following in order:
1. It saves INT 24h at `0000:0090` and points it at `191F:0039`, which returns AL=0.
2. It opens the file through `191F:0043` (names are compared, so the handle stays
   open between loads).
3. It seeks with AH=42h AL=0 to `file_off + reloc paragraphs`.
4. It reads `min(paras, end-start)` paragraphs with AH=3Fh, in chunks of up to
   `FFF0h` bytes, into `start` (`191F:0110`).
5. It seeks back to `file_off` and reads the relocations 32 at a time into
   `1906:000E`, then applies them.
6. It sets bit 15 and restores INT 24h.

Finding the file (`191F:0043`/`00BE`/`043A`/`046D`):
- DOS 2+ uses AH=3Dh with AL = 0 (or 30h if the AH=EFh probe changed BX) on
  `"nfl.EXE"` (relative, so the current directory). If that fails, it prepends each
  `PATH=` entry in turn.
- If every attempt fails, it prints `Place Program Disk in drive and press any key.`
  at row 24, column 15 (INT 10h AH=02, INT 21h AH=09). It then waits with AH=07,
  blanks the line, and retries.
- The DOS 1.x path is FCB based (AH=29h, 0Fh, 1Ah, 27h).

**The emulator must therefore resolve `nfl.EXE` case-insensitively in the
current directory and support seek/read on it while it is open.**

---

## 4. Compiler and C runtime

Evidence:
- Large model, `push bp / mov bp,sp / sub sp,n ... mov sp,bp / pop bp / retf`
  frames, far data pointers, and arguments pushed right to left as `mov ax,imm / push ax`.
- Start-up at `13F3:0007`: `FNINIT`, DS = `23D6`, a memory check against PSP:0002,
  a stack of at most 64 KB (`0x1000` paragraphs), AH=30h (the version is kept at DGROUP:3C2E),
  AH=4Ah shrink, the command tail copied onto the stack, BSS `DGROUP:514A-B3A1`
  zeroed, then `call 15D7:0006` (`_main`) and `13F3:0A3C` (`exit`).
- Runtime messages, `$`-terminated for INT 21h AH=09: `NEEDS DOS 2.0$`,
  `TOO MANY ARGS`, `SPRINTF$`, `NO CORE$`, `ALLOC$`, `FREE$`, `NULL alloc`, and
  `:0000$` (exit prints the caller's `SSSS:OOOO` with a DAA hex trick, `13F3:0A8E`).
- DOS/BIOS interface:
  - `int86(intno, &in, &out)` uses **one 8-word register struct
    `{ax,bx,cx,dx,si,di,ds,es}`** and **returns the FLAGS word**. It special-cases
    INT 25h/26h, which leave flags on the stack (`13F3:0717`).
  - `intdos(&in,&out)` uses the same struct (`13F3:0AA5`).
  - `bdos(ah, far *dx)` (`13F3:0A2D`).
  - `outp(port,val)` and `inp(port)` (`13F3:0A14`/`0A22`).
  - Both `switch` forms call helpers with inline tables (`13F3:00E7`: min, count, default,
    targets; `13F3:07DC`: n, values[n], default, targets[n]).
  - An IEEE-double software float package.

Conclusion (**probable, not proven**): **Computer Innovations C86** (large
model). Its documented `sysint(intno, &in, &out)` takes exactly this 8-register
`struct regval {ax,bx,cx,dx,si,di,ds,es}` and returns the flags. C86 also
provides `sysint21`, `bdos`, `inportb`/`outportb`. The other candidates do not
fit:
- Microsoft C has `R6xxx` run-time errors and `union REGS` plus a separate `struct SREGS`.
- Lattice C has `int86(int, union REGS*, union REGS*)` and different start-up
  messages.
- DeSmet C88 is small model only.
- Turbo C (1987 and later) carries Borland strings.

No compiler name string survives in the binary, so this stays an inference.
The linker is **PLINK86** (`Copyright (C) 1984 by Phoenix Software
Associates Ltd.` at `191F:018C`).

---

## 5. Interrupt and port cross-reference

`xref.py` resolves the AH value through a small constant evaluator over the
basic block. This is the full list from reached code:

| Service | Sites (examples) | Purpose |
|---|---|---|
| INT 10h AH=00 AL=mode | `15D4:002A` (`setmode(m)`, AX = m) | called from `159F:0037` (text) and `15A3:0028` (graphics) only if `getmode() != m` |
| INT 10h AH=01 | `130A:0185` (CX=0C0D for mode 7), `130A:01D2` | cursor shape: restore / hide (`CH \|= 20h`) |
| INT 10h AH=02 | `1575:0073`, manager `191F:047A` | set cursor position |
| INT 10h AH=03 | `130A:0153`, `130A:01AD` | read cursor shape |
| INT 10h AH=05 | `1550:002F` (AX = 0500h + page) | select display page |
| INT 10h AH=06/07 | `15B0:007F` (AX = 0600h+n or 0700h-n, BH = attribute in text modes, 0 in graphics) | scroll window / clear (`1548:0009` = clear screen) |
| INT 10h AH=09 | `157C:005B`, `1593:0056` (AX = 0900h + char, BX = page<<8 \| attr, CX=1) | write char + attribute |
| INT 10h AH=0B | `1537:0094` (BH=0 BL=border, text), `1537:00D0`/`0106` (BH=1 palette, BH=0 background, graphics) | `color(fg,bg,border/palette)` |
| INT 10h AH=0F | `130A:0129`, `15D1:0022` | get mode (mode 7 → mono B000) |
| INT 16h AH=00/01/02 | `1586:002F`, `158D:0031`, `15AC:002D` / `1582:0025` / `1599:002C` | read key (ASCII and scan returned separately) / kbhit via ZF in returned FLAGS / shift flags (Alt combinations) |
| INT 1Ah AH=00 | `1553:00C1` | tick count → random seed DGROUP:B2D0 (`main` and overlay 11) |
| INT 21h AH=09 | `15D7:0021`, `160E:00E6`, `1622:0045`, `1628:004F`, `13F3:000E`, `13F3:0A6B` | runtime fatal messages |
| INT 21h AH=02 | `13F3:0DF9` | stdout (handle 1) is written one character at a time with AH=02 |
| INT 21h AH=3C/3D | `16C0:0129` (AX = 3D00h + mode&3 + [bp+0Ch]) | `open`/`creat` (**guess** for 3C: `[bp+0Ch]` = -100h) |
| INT 21h AH=3E, 3F, 40, 41, 42 | `16C0:02D8`, `13F3:085B`, `13F3:0B7E`/`0E53`, `16A0:0044`, `13F3:0D30` | close, read, write (0E53 → handle 2), unlink (saved games), lseek AL=1 |
| INT 21h AH=44 AL=00/01 | `15D7:01B7`, `16C0:0158`, `16C0:019A` | IOCTL get/set device info (sets raw bit 20h on devices) |
| INT 21h AH=45 | `15D7:022E` | dup handle |
| INT 21h AH=2C | `0AAB:0D52` | time for the on-screen clock ("%2d:%02d PM") |
| INT 21h AH=30, 4A, 4C | start-up / exit | |
| out 61h / 43h=B6h / 42h | `15B8:0037`, `15BC:0023..0085` | speaker on/off and PIT channel 2 tone |
| in 3DAh, out 3D8h | `150F:002D`/`001A` | CGA snow avoidance, see below |

There are no `in`/`out` instructions with an immediate port anywhere. No code
touches ports 20h, 21h, 40h, 60h or 64h, or the CRTC.

Video segment immediates (reached code): `14EC:000D` B000, `14EC:0016` B800,
`14D9:0054` B800 (mode-4 pixel), and overlay 7 `1CE3:0030`, `1CEF:0046`,
`1D02:0014`, `1D3C:0019`, `1D3E:001B`, and overlay 11 `1F94:0089` (B800 blits). The
"46 B800 references" in the earlier byte count were mostly `B8 00 xx` =
`mov ax,0xXX00` opcodes.

---

## 6. Video detection and modes

- `15D1:0004 getmode()` is INT 10h AH=0Fh and returns AL.
- `14EC:0000` (video init) does the following:
  - If `getmode()==7`, the video segment DGROUP:5035 is `B000`; otherwise it is `B800`.
  - The offset DGROUP:5037 is 0.
  - The column count DGROUP:5033 comes from the table at DGROUP:5041, which has
    one word per mode: 40, 40, 80, 80, 40, 40, 80, 80.
  - It calls `150F:000B`, which enables the retrace wait.
  - Text writes go to `seg:(page*1000h + (row*cols+col)*2)` (`150F:0048`).
- `159F:0000 settext(cols, colour)` computes
  `mode = (cols==80 ? 2 : 0) + (colour>0 ? 1 : 0)`, giving 0-3. It calls `setmode` only
  if the current mode differs. `main` calls `settext(80,1)` through
  `130A:01DB`, which selects **mode 3** unless the machine is already in mode 3. A
  real MDA BIOS keeps mode 7, and AH=0F then reports 7, so B000 is used. That is why
  README says to run `mode color` on dual-monitor systems.
- `15A3:0003 setgraphics(hi)` gives mode 6 if hi, else **mode 4**. It is called with 0
  from `130A:01EE` and from overlay 5 `196D:0190` (instant replay), followed by
  `color(3,0,0)`, which selects INT 10h AH=0B BH=1 BL=0 (palette 0) and background 0.
  Mode 4 drawing is direct: `14D9` plots pixels with x ≤ 319, y ≤ 199, colour ≤ 3,
  80 bytes per row and the odd bank at `+2000h`. Overlay 7 scrolls the field with
  `movsb` loops over 100 rows per bank (`1CE3`).
- Display type from `config.nfl` (DGROUP:515E):
  - The default is 0 if `getmode()==7`, else 1 (`196D:3056`).
  - Configuration menu entries 0-5 store 3, 0, 5, 4, 1, 2 (`196D:36FA-3753`).
  - `130A:0005` remaps text attributes to monochrome-safe ones for types 0, 3, 4, 5 and 6.
    Types 1 and 2 keep the colour attributes.
- **Snow avoidance** (`150F:002D`, before every string copy): while
  `(in 3DA & 8)==0` it loops, then it writes `out 3D8,25h`, which turns video off.
  After the copy, `150F:001A` writes `out 3D8,29h` (80x25 text, video on, blink).
  The byte flag `150F:000A`, which is self-modified (`mov [cs:0x000a],al`), disables
  the wait. It is set by `150F:0003` when output is redirected to an off-screen
  buffer (`1521:0003`) and cleared by video init.
  **Emulator: toggle bit 3 of 3DAh, otherwise the game spins forever. Do not
  let the 3D8h writes blank or re-mode the display. Render from the BIOS
  mode.**

## 7. Keyboard, timing, sound

- `1582:0007 kbhit()`: INT 16h AH=01. It returns the key word if ZF is clear,
  else -1.
- `1586:0007` / `15AC:0003` / `158D:0009` are non-blocking reads (they return -1 if
  `kbhit()` is false). They return ASCII and scan code, or one of them (`sar ax,8`
  gives the scan code).
- `1599:000E` returns the INT 16h AH=02 shift state, used for the Alt-key commands
  such as `[Alt][E]`.
- `158C:0003` flushes type-ahead by copying `0040:001A` to `0040:001C`.
- There is no real-time pacing. Delays are busy loops scaled by the configured
  "Delay" (DGROUP:5160, default 100; `main` stores `delay/100` in DGROUP:5168).
  INT 1Ah is only the random seed and INT 21h AH=2Ch only the on-screen clock. A
  fixed initial tick count therefore gives reproducible games.
- Sound is PIT channel 2 through port 61h bits 0-1.

## 8. Start-up path of `main` (`0000:0000`)

1. `18B4:00CE` → overlay 11 `196D:0000`: title, `config.nfl` (or the
   CONFIGURATION screen), command line.
2. DGROUP:5168 = delay/100.
3. `settext(80,1)`, `color(2,0,0)`, clear, hide cursor.
4. `setpage(0)` unless display type is 0.
5. `14EC:0000` video init.
6. `1553:00A3` random seed.
7. `18B4:00DA` → overlay 11 `196D:023C` (team selection and so on).
8. `130A:0005(2)` ...

### Command-line switches (`196D:011E`, overlay 11)

Each `argv[i]` must start with `/`. The next character goes through a sparse
`switch` (inline table at `196D:01F9`, both letter cases):

| Switch | Effect | Flag |
|---|---|---|
| `/I` | instant replay on | DGROUP:4FBD = 1 |
| `/S` | Season.nfl mode, ignored if `/R` was given first | DGROUP:4FB3 = 1 |
| `/R[n]` | restore a saved game, ignored if `/S` was given first. Optional digit 1-9 picks game *n* (`[B12C]`, 0 otherwise) | DGROUP:4FB5 = 1 |
| `/L` | log mode | DGROUP:4FBB = 1 |
| `/T` | undocumented, purpose not traced | DGROUP:4FB9 = 1 |
| `/A` | undocumented, purpose not traced | DGROUP:44A0 = DGROUP:44A2 = 1 |
| `/D` | accepted, does nothing | — |

## 9. Open questions

- The exact roles of overlays 6 and 9 (marked guess above).
- What the undocumented `/T` and `/A` switches do.
- Which `[bp+0Ch]` values reach `16C0:0005` (to confirm `creat` = AH 3Ch).
- C86 identification is by API shape only.

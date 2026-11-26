# Static recompilation of NFL.EXE

The Godot project no longer interprets 8086 instructions. A translator,
`tools/recomp/recompile.py`, which the user runs once on their own `NFL.EXE`,
turns every reachable 8086 routine of the program into a GDScript function
ahead of time. At run time Godot calls those functions directly, on top of a
native host layer (`godot/host/`): DOS file services on `FileAccess`, the BIOS
keyboard and video services, a re-implementation of the PLINK86 overlay
manager, the PC speaker, the HD text renderer and the CGA renderer. This is
the model used by static-recompilation projects such as the N64 recomps.

The generated code is derived from a copyrighted binary. It is written to
`godot/recomp/`, which is gitignored, and is never committed. Only the
translator, the host layer, the tests and these docs are in the repository.

```sh
python3 tools/recomp/recompile.py /path/to/NFL.EXE [--bundle-data]   # -> godot/recomp/ (about 2 s)
```

`NFL.EXE` is needed only for this step. The game in Godot never opens it (see
"No NFL.EXE at run time" below).

## No NFL.EXE at run time

Besides the scripts, the recompiler writes `godot/recomp/program.bin`, about
300 KB, read by `godot/host/program_blob.gd`. It holds:

* the MZ header values: CS:IP, SS:SP, minalloc, maxalloc and the relocation
  count;
* the root image, already relocated for load segment 0122h;
* the overlay section of the file: each overlay's fixup table and image, as
  stored in the EXE;
* the program's file name;
* the SHA-1 of the source EXE. It must equal `PROGRAM_SHA1` in
  `recomp_index.gd`, otherwise `RecompHost.setup` stops with "program.bin
  does not match the recompiled scripts".

`DOS.install_program(blob, tail)` replaces the MZ loader. It builds the same
MCBs, environment (`C:\NFL.EXE`), PSP, memory and registers as loading the
EXE would, copying the pre-relocated image. It also registers the program's
name as a **read-only virtual file**, served by `MemFile` from the overlay
section at the original file offsets, so it exists on every drive and in
every directory without a host file.

The only code that opens the program file is the overlay manager: once, at
start-up, to preload overlay 12, keeping the handle open. That is confirmed by
the oracle's file log, which records one open of `nfl.EXE` per run, before
`config.nfl`. The native manager therefore makes the same DOS calls as the
original (open, seek, read in FFF0h chunks, read the fixup tables into
`1906:000E`), and they are served from the virtual file. It applies the
overlay fixups itself at load time, as PLINK86 does, instead of storing
pre-relocated overlay images. The reason is that the DOS call sequence, the
file handle and the fixup buffer must match the original for the lockstep
test: the deterministic clock counts service calls, and `INT 1Ah` seeds the
game's random numbers from that clock.

`recompile.py --bundle-data` also copies the game's data files (everything in
the EXE's folder and its subfolders except `.EXE`, `.COM` and `.BAT`) to
`godot/recomp/game/`. `main.gd` uses it as a read-only drive root (writes go
to `user://dos_c`) when no other data folder is found, so an export with the
non-resource filter `recomp/*.bin, recomp/game/*` is fully standalone. Data
folders are recognised by `LEAGUE.NFL`. None of them needs `NFL.EXE`.

## Pipeline

| Step | Module | What it does |
|------|--------|--------------|
| Loader | `image.py` (on `tools/disasm/mzlib.py`) | Root image and the 12 overlays at the **fixed load segment 0122h** (PSP 0112h, the segment the host's DOS loader uses). MZ relocations and overlay fixups are applied at translation time, so every far address is a constant. |
| Decoder | `x86.py` | 8086 decoder with the operand rules of the oracle CPU, and the flags each instruction reads, writes or may write. |
| Discovery | `analysis.py` | Recursive descent from the entries: the C start-up `13F3:0007`, every one of the 93 overlay-thunk targets (each in its overlay), and the dynamic targets of the coverage profile. Functions are identified by `(unit, CS, IP)`, where the unit is the root or an overlay. The same segment range is translated separately per overlay. Code shared between functions is translated into each of them. |
| Liveness | `analysis.py` | Interprocedural flag liveness (`fin`, flags read before written from a function's entry, and `fout`, flags live after its return) and register DEF sets, iterated to a fixed point. |
| Backend | `gdgen.py` | GDScript, one function per discovered function, grouped into about 23 scripts (`u_r_N.gd` root, `u_oNN_M.gd` overlays) of at most about 40,000 lines each, plus `recomp_index.gd` with the dispatch table. |

### Generated code

```gdscript
func f_r_1515_0717() -> int:          # unit root, CS:IP 1515:0717 (13F3:0717 + 0122)
	var r: PackedInt32Array = st.r     # shared with the host (reference)
	var m: PackedByteArray = mem       # the 1 MB memory
	var ax: int = r[0]                 # registers the function mentions
	...
	var b: int = 0x717                 # current basic block
	while true:
		if b == 0x717:
			...                        # straight-line code of the block
			if zf != 0:                # jcc
				b = 0x72B
			else:
				b = 0x725
		if b == 0x725:                 # fall-through costs one comparison
			...
```

* **Registers** are typed `int` locals (`ax cx dx bx sp bp si di es ss ds`).
  CS is a constant of the function. The shared `CpuState` (`st`) holds the
  authoritative copy between functions. A function loads the registers it
  mentions on entry and spills them before every call and on return. After a
  call it reloads only the registers in the callee's DEF set.
* **Flags** are int locals, computed only where liveness says they are read.
  A flag-setting instruction followed by a conditional jump, whose flags are
  dead afterwards, is fused into one comparison (`cmp a,b / jl` becomes
  `(a ^ 0x8000) < (b ^ 0x8000)`). Where flags are live, the semantics are
  those of the oracle: AF, OF, and the flags after MUL, IMUL, shifts and
  rotates (including a CL count of 0, which leaves the flags unchanged) and
  after DAA, DAS, AAA and AAS.
* **Memory** is accessed as inline `m[...]` with 20-bit address arithmetic.
* **Basic blocks** are sequential `if b == IP:` tests inside `while true:`.
  For big functions they are chunked into groups of 12 under `if b < LIMIT:`.
  Any intra-function jump therefore works. Backward jumps `continue`.
* **Back-edges.** A taken backward conditional jump, LOOP/JCXZ or direct JMP
  spends one unit of the local poll budget `pl`. It is loaded from
  `st.eb` on entry and stored back around every call and return. When the
  budget runs out, the function stores its whole state and calls
  `host.edge_poll(cs:ip)`. That books the back-edges (in the deterministic
  test clock they count as polls), keeps the realtime clock going, delivers a
  pending timer interrupt, and stops the program if asked. In realtime mode
  the budget is 4096 back-edges.
* **Calls** push the return address on the emulated stack, because the
  program reads its own frames through BP, and then call the GDScript
  function. Every function returns the CS:IP its RET popped, packed as
  `(cs << 16) | ip`. The caller compares that value with the address after
  the call. Any other value is propagated upward through `host.unwind()`:
  some outer frame expects it. This is how the soft-float package returns:
  its register-saving helpers return with `jmp [bp+10h]` and the routines'
  RETF skips a frame. `ABORT` (-1) unwinds the whole program on exit or
  stop.
* **Switch statements**: the C runtime's helpers (`13F3:00E7` dense,
  `13F3:07DC` sparse) are never called. Each call site becomes a direct
  multi-way branch over the targets parsed from its inline table. It
  reproduces the helper's register, flag and stack effects.
* **Overlay thunks** (`call far 18B4:xxxx`) become
  `host.ovl_ensure(n)` followed by a direct call into overlay n's function.
* **Indirect transfers**: an indirect `jmp` within the segment becomes a
  match over its known targets. Everything else goes through
  `host.dispatch(cs:ip)`, which maps `(resident overlay, address)` to a
  function. A miss is a hard, logged error that names the address
  (`RecompHost.fatal`). There is no interpreter to fall back to.

### The dynamic profile and the hints file

Static discovery cannot see every target of the few indirect transfers.
`tools/recomp/profile_oracle.py` runs the Python oracle (`tools/refemu/`)
through the `boot` scenario (configuration, team selection, a long game with
instant replay) and the `season` scenario (`/s`). It records every indirect
CALL/JMP target, and every RET/RETF/IRET whose target is not the instruction
after the matching call (tracked with a shadow call stack). It also records
every executed instruction address per resident-overlay set. The translator
reads the compact export `tools/recomp/profile.json` (390 transfers,
addresses only). It found:

* the `int86()` continuation `13F3:0766`, which the ROM stub's IRET returns to;
* `13F3:0E6D`, a `pop/pop/pop/pop/push/push/push/push/retf` trampoline that
  calls through a far pointer. Its RETF is translated as `host.dispatch()`;
* the soft-float helpers' `jmp [bp+10h]`, and the RETFs that unwind past
  them.

Statically, the translator also makes every far code pointer stored in the
initialised data segment (a relocated segment word in overlay 12 that points
into root code) a dispatcher entry. There are 5 such pointers, to 3 functions:
`0A77:0008`, `160E:0006` and `13F3:0ADF` (link segments).

`tools/recomp/hints.json` (NFL.EXE) and `hints_season_com.json` (SEASON.COM)
hold generic, address-keyed patches with evidence.
Two are needed. `13F3:0128` and `13F3:0174` are register-saving helpers that
return with `jmp [bp+10h]`, which is their own return address. So the
targets of those jumps are the return addresses of every call to the helpers,
including float routines the oracle runs did not exercise. The file format is:

```json
{"ret_via_jmp": [{"site": "13F3:017E", "helper": "13F3:0174", "evidence": "..."}],
 "jump_targets": [{"site": "SSSS:OOOO", "targets": ["SSSS:OOOO", ...]}]}
```

Segments are link segments (load base 0). `jump_targets` adds static targets
to an indirect jump. It is unused for v2.01.

## SEASON.COM (a Turbo Pascal 3 .COM program)

`recompile.py <game folder>` translates both programs (`recompile.py NFL.EXE`
still translates just NFL.EXE). SEASON.COM gets its own units, index and
`program.bin` in `godot/recomp/season/`. It is loaded at the same fixed PSP
(0112h) with CS = DS = ES = SS = 0112h and entered at PSP:0100. Its profile
is `tools/recomp/profile_season_com.json`, from `profile_oracle.py seasoncom`
(the key script `scenarios.py season` uses). Its hints are
`tools/recomp/hints_season_com.json`. The Turbo Pascal 3.0 runtime uses
patterns the C program does not, and the profiler and translator handle them
generically:

* **Returns by jump.** Runtime helpers pop their return address and return
  with `jmp bx`, `jmp si` or `jmp [mem]`. The profiler keeps a shadow call
  stack of (return address, callee). An indirect JMP to the innermost call's
  return address is logged as `jmpret`, and the translator turns that JMP
  into a return.
* **Inline arguments after a CALL.** String constants are placed right after
  the CALL (`call 295D / db 5,'Bills'`), and the helper returns past them.
  If a helper's return lands at return address + 1 + the byte there, the
  profiler logs the callee as `pstr`. The translator then continues every
  call to it after the string, statically, including call sites the run never
  reached. Other one-off inline blocks (the start-up's parameter blocks)
  are logged per call site as `skip`. The run-time error printer `0A81`,
  which takes a NUL-terminated inline string and is never reached in a normal
  run, is an `inline_cstr` hint with evidence.
* **A hooked timer interrupt.** At start-up the runtime hooks INT 08h to
  calibrate `Delay`: it counts loop iterations between two ticks, and the
  handlers (`01C4`, `01DB`) chain to the old vector. The profiler logs the
  handlers (`irq08`) and they become dispatcher entries. When INT 08h is
  hooked, a tick makes IRQ 0 pending instead of updating the BIOS clock. The
  next back-edge (with IF=1) delivers it: `RecompHost.deliver_irq` pushes
  FLAGS/CS/IP and dispatches to the handler. The handler's `jmp far` to
  F000:E400 runs the native ROM timer (tick, INT 1Ch, EOI, IRET) and
  returns to the interrupted code.
* Paths into bytes that do not decode are not translated, and reaching one
  is fatal at run time. Examples are the code after a CALL that terminates
  the program, or after an inline-argument helper the profile never saw.
  The translator warns about each one (three in SEASON.COM, all after
  `Halt`/AH=00h).

In Godot, `--program=season` runs SEASON.COM instead of NFL.EXE. It writes
`season.nfl` (and its `config.sea`) to the writable team drive, where
`--args=/s` then finds it.

## Host layer (`godot/host/`)

| File | Role |
|------|------|
| `cpu_state.gd` | `CpuState`: registers, flags and the 1 MB `PackedByteArray`. There is no decoder. |
| `machine.gd` | ROM stubs, IVT and BDA set-up, I/O ports (CGA status toggle, speaker, PIT divisor), keyboard queue, the 18.2 Hz clock, and `service(n)`, the BIOS/DOS dispatcher with blocking waits. |
| `bios.gd`, `dos.gd` | The services, unchanged from the interpreter port except for the keyboard entry `key_event(scancode)`, the program set-up from `program.bin` (`install_program`, which replaces the MZ loader) and virtual files. |
| `program_blob.gd`, `mem_file.gd` | The `program.bin` reader and the in-memory read-only file behind the virtual `NFL.EXE`. |
| `recomp_host.gd` | `RecompHost`: loads and links the units, re-creates the PLINK86 start-up natively, the native overlay manager, ROM-stub calls (`do_int` for `INT n`, `iret_to_rom` for `int86()`), `dispatch`, `edge_poll` (back-edge budget and timer interrupts), and `fatal`. |
| `recomp_unit.gd` | Base class of the generated scripts (`st`, `host`, `mem`, `par`). |
| `recomp_loader.gd` | Loads the generated scripts on the main thread, one per frame. |
| `key_script.gd`, `smoke.gd`, `check_scripts.gd` | The smoke mode (deterministic key scripts, screenshots, timing) and the compile check, both used by `tools/godot_check.sh`. |
| `video.gd`, `font_cp437.gd` | The CGA/text bitmap renderer and the font. |

**Overlay manager.** The manager's code at `191F` is not translated.
`ovl_ensure(n)` follows `191F:0288`. It walks the parent chain and clears
the *loaded* bit of overlapping overlays. It then loads overlay n with the
same DOS calls on `nfl.EXE` (the virtual file) as the original: open once (the handle stays
open), seek to the code, read in chunks of FFF0h bytes, seek back, read the
fixups 32 at a time into `1906:000E`, and apply them. It does the same
bookkeeping in the overlay table and in the manager's data segment, and
saves and restores INT 24h. So memory, DOS file handles and the order of
DOS calls are the same as the original's. The start-up (`191F:02FB`) is
re-created the same way: DOS version check, PATH lookup, table fix-up,
network probe and the preload of overlay 12. It leaves the registers exactly
as its `jmp far 13F3:0007` does. The only thing it does not reproduce is the
manager's own scratch values below the stack pointer (see Limitations).

**BIOS calls.** `int86()` pushes FLAGS/CS/IP and IRETs into the IVT. In the
generated code an IRET whose target is in `F000` calls
`host.iret_to_rom()`. That runs the native service, performs the stub's IRET
(popping the flags image the service patched, which is how `kbhit()` sees
ZF), and dispatches to the continuation. The few real `INT 21h` instructions
call `host.do_int(n)`. Port I/O calls `host.port_in/port_out`; `3DAh` bit 3
toggles.

## Thread model (Godot)

The translated C code is deeply nested and blocking: it waits for keys
inside the C runtime. GDScript has no coroutine stack switching, and `await`
does not work across ordinary calls. So the program runs on its own
**`Thread`** (`main.gd`, `_program_thread`):

* The main thread loads the generated scripts, one per frame
  (`RecompLoader`), so the window stays responsive and the HUD shows
  progress. Compiling GDScript off the main thread is avoided on purpose: it
  is the less-trodden path in Godot 4.3. It then links them
  (`RecompHost.setup`) and starts the program thread, which only runs
  `RecompHost.run()` until the program exits.
* A blocking service (INT 16h AH=00h with no key, for example) waits on a
  `Semaphore`. The main thread posts it every frame while the program
  waits (`Machine.wake()`), and immediately when a key arrives.
* The BIOS tick follows the wall clock times the speed setting. It is
  updated lazily whenever the program calls a service or reads a port, and
  every 4096 loop back-edges (`host.edge_poll()`). The game's delays poll INT 21h
  AH=2Ch and `kbhit()`, so this paces it correctly. Idle `kbhit()` polling
  sleeps 0.5 ms every 64 polls, so busy-waits do not burn a whole core.
* The main thread renders video memory (HD text or the CGA bitmap), feeds
  scancodes through `Machine.key_scancodes()`, and reads the speaker state.
  The only shared mutable state is the key queue (guarded by a `Mutex`) and
  the `abort`, `paused` and `waiting` flags. Video memory is read without
  locking: a torn frame is harmless.
* Reset, folder change and quit call `Machine.request_abort()` and
  `Thread.wait_to_finish()`. `abort` makes the next service or back-edge
  poll return `ABORT`, which unwinds every frame.

`tools/recomp/thread_smoke.py` exercises this model with Python threads. It
runs the transpiled GDScript with the realtime clock, a 60 Hz main loop
feeding keys, and abort plus join.

## Verification

All of it runs in this sandbox, without Godot. The **tested code is the
GDScript**: `tools/recomp/lockstep.py` transpiles `godot/host/*.gd` and the
generated `godot/recomp/*.gd` to Python with `tools/gdport/gd2py.py`, then runs
them on the Godot API shim `tools/gdport/gdrt.py`.

```sh
G=/path/to/nfl
python3 -I tools/recomp/profile_oracle.py --game-dir $G boot season      # oracle profile + coverage (~10 min)
python3 -I tools/recomp/profile_oracle.py --export tools/recomp/profile.json build/recomp/profile_*.json
python3 -I tools/recomp/recompile.py $G/NFL.EXE
python3 -I tools/recomp/coverage.py build/recomp/profile_boot.json build/recomp/profile_season.json
python3 -I tools/recomp/test_translate.py --count 3000 --length 20     # random snippets vs the oracle CPU
python3 -I tools/recomp/lockstep.py --game-dir $G season                 # /s game, STATS.LOG (recompiled side: NFL.EXE deleted)
python3 -I tools/recomp/lockstep.py --game-dir $G boot                   # config, teams, game, instant replay
python3 -I tools/recomp/thread_smoke.py --game-dir $G [--season-com]
python3 -I tools/recomp/lockstep.py --game-dir $G seasoncom              # SEASON.COM -> season.nfl
python3 -I tools/recomp/lockstep.py --game-dir $G season --season-nfl build/recomp/ls-recomp-seasoncom/89/SEASON.NFL
python3 -I tools/recomp/smoke_test.py                                    # main.gd --smoke, via gd2py
tools/godot_check.sh                                                     # on your machine: official Godot 4.3
(cd godot && gdlint main.gd text_screen.gd host/*.gd)
(cd godot/recomp && gdlint *.gd)            # relaxed gdlintrc written by the recompiler
grep -rn "match op\|fetch8\|exec_op\|modrm" godot/ --include=*.gd   # nothing: no interpreter
```

**Instruction semantics.** `test_translate.py` assembles random snippets with
nasm and runs each one twice: in the oracle CPU, and translated by this
pipeline to GDScript and then to Python. The snippets cover ALU ops,
INC/DEC/NEG/NOT, shifts and rotates by 1, an immediate or CL (including
counts of 0 and above 16), MUL/IMUL/DIV/IDIV, the BCD adjusts, LAHF/SAHF,
PUSHF/POPF, XCHG, LEA, LES, string instructions with and without REP,
compare-and-branch pairs (the fused paths) and LOOPs. The function returns
with every flag live. All registers, the flags, DF and memory must agree.

**Lockstep.** Both sides run with the same deterministic clock: one BIOS tick
every 64 *polls* and one whenever a service blocks for input. Polls are
service calls through a ROM stub, port reads and loop back-edges. The oracle
counts a taken backward Jcc/LOOP/JCXZ/JMP outside the ROM and the overlay
manager, which is exactly what the generated code counts. A hooked timer
interrupt is delivered at the next back-edge on both sides. Keys from the scenario script are
delivered at ticks. So both programs see the same time at the same logical
points. The harness compares:

* at every service call, the service number and its input registers, plus SP
  and SS;
* every 2,000 service calls and at the end, a CRC32 of the 1 MB memory;
* the text screen at every `{when:...}` step of the script;
* the video memory at every tick in graphics mode (the instant-replay frames);
* every file the program wrote, byte for byte.

The recompiled side deletes `NFL.EXE` from its copy of the game folder and
sets the program up from `godot/recomp/program.bin`, as `main.gd` does. It
logs every open of the program file, all served by the virtual file. The
oracle (`tools/refemu/`, unchanged) still loads the real EXE.

The oracle trace is cached in `build/recomp/trace_*.pkl`. `--diff-at N` lists
the memory ranges that differ at service call N. That is how start-up and
overlay-manager differences were bisected.

Results are in `docs/EMULATION_NOTES.md` ("Static recompilation").

**Smoke mode.** `godot --headless --path godot -- --smoke=<boot|seasoncom|season>`
does the following:
* runs a program on its real program thread with the deterministic clock and
  a GDScript key script (`host/key_script.gd`, the same script language);
* saves PNG screenshots and text dumps;
* prints timing: wall time, BIOS ticks, service calls per second, dispatched
  calls, timer interrupts and main-thread frame times;
* exits non-zero on any error, dispatch miss or timeout.

`tools/recomp/smoke_test.py` runs the same `Smoke` class through gd2py with
Python threads. `tools/godot_check.sh` runs it with the official binary.

## Godot limits (audit for 4.3)

| Concern | Status |
|---------|--------|
| `load()` of scripts from a worker thread | Avoided. `RecompLoader` loads on the main thread, one script per frame, and the program thread only runs code. |
| `Thread` / `Semaphore` / `Mutex` | `Thread.start(Callable)` plus `wait_to_finish()` on reset and quit (`_exit_tree`). A `Semaphore` wakes the program while it blocks. The `Mutex` guards only the key queue. These are all documented 4.3 APIs. Exercised here with Python threads (`thread_smoke.py`, `smoke_test.py`). |
| Thread stack and call depth | The program thread nests at most 17 recompiled functions (NFL.EXE boot and season) and 6 (SEASON.COM), measured by `lockstep.py`. GDScript's `max_call_stack` is 1024, and the native stack per GDScript call is a few KB. That stays well inside even macOS's 512 KB default for secondary threads. |
| `FileAccess` on `res://` in exports | `program.bin` and `recomp/game/*` are not resources. The export preset needs the non-resource filter `recomp/*.bin, recomp/game/*` (see `godot/README.md`). Otherwise use `user://dos_c` or `--dos-root`. |
| `PackedByteArray` | It is passed by reference inside Variants in 4.x. Every unit, the host and the renderer share one 1 MB buffer: no copy-on-write and no per-access copies. The generated code indexes a typed local. |
| `@warning_ignore` names | Only valid names (`unused_variable`, `integer_division`). An unknown name is a hard parse error in 4.3 ("Invalid warning name"). |
| File and function size | 24 scripts of up to 750 KB. The largest function has 19,187 lines and at most about 30 locals plus temporaries. The longest `if/elif` chain has 16 branches, and the deepest indentation is 7. `match` is not used. GDScript has no documented limit there. Compile time is measured by `check_scripts.gd`. |
| Integer semantics | Every value is masked to 8, 16 or 20 bits. `/` and `%` are used only on non-negative operands, with IDIV done on absolute values. `>>` on a negative is used only for SAR (arithmetic in 4.3, as in C++ on every supported compiler). There are no negative shift counts, and `<<` stays far below 2^63. |

## Limitations

* **Stack residue of the overlay manager.** The native manager does not
  reproduce the scratch words the original leaves *below* the stack pointer.
  The C code later reads some of them as the uninitialised fields of an
  8-word `REGS` struct passed to `int86()`. They are, for example, the SI,
  DI and ES of an INT 21h AH=3Dh call, which DOS ignores. Lockstep therefore
  compares only each service's input registers, and the memory CRC leaves out
  the program's stack segment (`3832h`) and the manager's 256-byte private
  start-up stack.
* **Coverage-driven targets.** Dynamic targets come from the oracle runs and
  the hints. A path the runs did not exercise that uses a new indirect target
  stops with a clear `dispatch miss at SSSS:OOOO`. Fix it by running the
  profiler on a scenario that reaches it, or by adding a `jump_targets` hint.
  The only indirect site without targets is the DOS 1.x exit path
  (`jmp far [si]` at `13F3:0A8C`), which DOS 3.30 never takes.
* Word accesses at offset FFFFh of a segment do not wrap within the
  segment, and writes to the F000 ROM area are not blocked. The program
  does neither.
* One fixed binary: the translator applies relocations for load segment
  0122h, and `install_program` refuses to run if the DOS loader picks
  another one.
  The address-keyed hints and the profile are for v2.01 (8/8/89).
* **Not run in Godot.** No Godot binary could be used here. GDScript-specific
  behaviour is checked only by `gdparse`/`gdlint` and by the transpiled run:
  compile time of about 490,000 generated lines, runtime typing, `Thread`,
  `Semaphore` and `Mutex`, and speed.

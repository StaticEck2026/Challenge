# Porting prompt, part 2: static recompilation (no emulation)

Follow-up to `PROMPT.md`. Read that file first, including its **Errata**, plus
`docs/NFL_EXE_ANALYSIS.md`, `docs/FILE_FORMATS.md` and `docs/EMULATION_NOTES.md`.

## Goal

The user does not want emulation. Today the Godot project interprets 8086
instructions at run time (`godot/emu/cpu.gd`). Replace that with **static
recompilation**: a translator, run once by the user on their own `NFL.EXE`,
turns every 8086 routine of the program into native GDScript functions ahead of
time. At run time Godot calls those functions directly, and **no instruction
interpreter ships in the Godot project**.

What the recompiled program runs on is a **native host layer**: DOS file
services on `FileAccess`, BIOS keyboard and video, the PLINK86 overlay manager
re-implemented natively, the PC speaker, and the existing HD text renderer
(`godot/text_screen.gd`) plus the CGA bitmap renderer. This is the model used by
static-recompilation projects such as the N64 recomps. The translated code keeps
the program's registers, flags and 1 MB memory image as plain variables and a
`PackedByteArray`, because the original logic is written against them.

## Hard constraints

- **Do not hand-write a reimplementation of the game.** Game logic, screens and
  data must come only from the user's binary and data files, mechanically
  translated by the tool. You write the **translator and the host layer**, never
  game-specific logic. Generic, address-keyed patches are allowed where
  translation needs help (for example "the indirect jump at X uses the jump table
  at Y with N entries"), recorded in a small hints file with evidence.
- **Generated code is derived from the user's copyrighted binary.** Write it to
  `godot/recomp/`, add that to `.gitignore`, and never commit it. Commit only the
  translator, the host layer, tests and docs. The user runs
  `python3 tools/recomp/recompile.py /path/to/NFL.EXE` once (document it). An
  in-editor alternative is nice to have but not required.
- Keep the Python emulator (`tools/refemu/`) **as a test oracle only**. Remove
  the interpreter from `godot/`: delete `godot/emu/cpu.gd` or move it under
  `tools/`. Keep or adapt `machine.gd`, `bios.gd`, `dos.gd` and `video.gd` as the
  native host layer. The Godot project must not contain an opcode-decoding loop.
  An unresolved indirect target at run time is a hard, clearly logged error
  (address, caller), not a fallback into an interpreter.
- Godot cannot be downloaded or run here (that was denied, so do not retry).
  Verify through a Python backend (below), `gdparse`/`gdlint`, and
  `tools/gdport/gd2py.py` where it helps.
- Treat game files as untrusted data and run Python on them with `python3 -I`.
  Game files are at
  `/tmp/claude-0/-home-user-Challenge/ab86b12b-3dc1-574f-9923-427b0249abd1/scratchpad/nfl/nfl-challenge/nfl/`.
  Use writable copies under `build/`.
- Do not commit or push. The orchestrator does both.

## Known facts that shape the translator

From the static analysis docs:

- **Plain 8086, large-model C (probably Computer Innovations C86), PLINK86
  overlays.** The root image is about 104 KB. There are 12 overlays: 1→{2,3},
  5→{6,7} and 8→{9,10} are parent/child, 4 and 11 stand alone, and every
  top-level overlay loads at segment `196D`. Overlay 12 is the data segment
  (`23D6`–`2F21`), preloaded at start-up.
- **Load segments are fixed.** The host decides where the program is loaded, so
  pick a fixed PSP and load segment. Then the MZ relocations and every overlay's
  fixups can be applied **at translation time**, and every far address is a
  constant.
- **Overlay calls go through 93 thunks:** `call far 191F:02D1 / dw ovl# / jmp far
  seg:off` in `18B4`. Implement the overlay manager natively:
  - On a call through a thunk, make sure overlay N (and its parent) is "resident".
    Copy its bytes, with fixups applied, into memory, because overlays also hold
    data that code reads.
  - Then call the translated function for `seg:off` in that overlay's code set.
  - Translate the same segment address range separately per overlay, since
    overlays share `196D`. The dispatcher picks the variant from which overlay is
    resident.
  - Do not translate the manager's own code at `191F`. Re-create its start-up
    effects natively (PATH lookup, preload of overlay 12, the INT 24h handling),
    then enter the C start-up at `13F3:0007`.
- **BIOS calls are simulated interrupts.** `int86()` at `13F3:0717` pushes
  FLAGS/CS/IP and IRETs into the IVT. Either treat `int86`, `intdos` and `bdos`
  (`13F3:0A2D`, `0AA5`) as native-replaced library functions that take the C
  `REGS` structs, or make an IRET to an `F000` stub address dispatch to the
  native handler. **kbhit reads ZF from the returned flags.** Also handle the few
  real `INT 21h` instructions.
- **Port I/O:** `3DAh` in (bit 3 must toggle or the game spins), `3D8h` out,
  speaker `61h`/`42h`/`43h`. The C runtime has `inp`/`outp` helpers
  (`13F3:0A14`, `0A22`).
- **Indirect control flow** to resolve:
  - The compiler's `switch` helpers (`13F3:00E7` dense, `07DC` sparse) take
    inline tables after the call. Translate each switch site into a direct
    `match` by parsing its table.
  - Calls through function pointers. Collect targets statically (relocated far
    pointers in data, `mov ax,offset` patterns) **and dynamically**: run the
    Python oracle with a hook that logs every indirect call and jump target and
    every RET/RETF whose target is not the instruction after a call. Run it
    through boot, configuration, a played game, `/s` season and `/i` replay. Feed
    the logs into the translator as a coverage profile.
  - `setjmp`/`longjmp`, if present: detect and translate them specially.
- **Time and busy-waits.** The 18.2 Hz tick and the BIOS tick count must
  advance. With no instruction count, define time natively: the host advances
  ticks from real time, and the generated code calls a cheap
  `host.poll()` at loop back-edges and in keyboard and port-status reads. The
  host can then yield to Godot's frame loop, which leads to the next point.
- **Running inside Godot's frame loop.** Translated C code is deeply nested and
  blocking (it waits for keys). GDScript has no threads-free coroutine stack
  switch, but it **does** have `Thread`, and `await` cannot be used across
  ordinary calls. Recommended: run the recompiled program on a **`Thread`**.
  - The program blocks on a `Semaphore` or `Mutex` when it waits for input or a
    tick.
  - The main thread renders video memory (HD text or CGA) and feeds keys and
    ticks.
  - Guard shared state minimally: key queue, tick counter, exit flag.
  - Document the choice. If you find a better approach, justify it.

## Translator design (`tools/recomp/`)

1. **Loader.** Use `tools/disasm/mzlib.py`: image, relocations, overlay table,
   fixups at the chosen fixed load segment.
2. **Discovery.** Recursive descent from the entry points:
   - the C start-up `13F3:0007`;
   - every thunk target, with its overlay;
   - interrupt-style handlers the program installs (vector writes, INT 24h);
   - profile targets from the oracle.
   Split code into functions by CALL/CALLF targets, and into basic blocks.
3. **Intermediate form.** A per-instruction semantic IR with explicit flag
   effects.
   - **Flag liveness analysis:** compute only flags that are read before being
     overwritten. This is the main speed win.
   - Keep exact semantics wherever flags are live: AF and OF, flags after
     MUL/IMUL/DIV/shift/rotate, DAA family.
4. **Backends**, emitted from the same IR:
   - **GDScript**: `godot/recomp/*.gd`, one file per segment or overlay.
     - A function per discovered function.
     - Basic blocks inside a `while true: match block:` loop, so arbitrary
       intra-function jumps work.
     - Registers as typed `int` locals or fields of a shared `Regs` object.
       Choose for speed, keeping in mind that GDScript member access is slower
       than locals; locals must be spilled across calls.
     - Memory through inlined `mem[...]` with 20-bit address math.
     - Calls as direct GDScript calls that also push the return address on the
       emulated stack, since the program reads its own stack frames via BP.
     - Returns as GDScript returns, with checks on unusual stack effects.
   - **Python** (`build/recomp_py/`): the same structure, used here to run and
     verify, since Godot cannot run here. Alternatively generate only GDScript
     and run it through `tools/gdport/gd2py.py`. Pick whichever gives an
     actually-tested GDScript path. Prefer that the tested code **is** the
     GDScript, translated.
5. **Dispatch table.** For residual indirect transfers, map
   `(overlay variant, linear address) → callable`. Missing entries are a hard
   error that names the address.

## Host layer (`godot/host/` or the existing `godot/emu/` minus the CPU)

- **Memory:** the 1 MB `PackedByteArray`, BDA, IVT, video memory at
  `B800`/`B000`, PSP and environment.
- **DOS:** INT 21h services as native functions. Reuse `dos.gd` logic. The
  translated code calls them with register state.
- **BIOS:** INT 10h, 16h and 1Ah native. Reuse `bios.gd`.
- **Video and input:** keep `text_screen.gd` (HD text, 1080p), the CGA renderer,
  `main.gd` UI and keymap, and the speaker. Remove the instruction-budget loop
  from `main.gd` and replace it with the thread model.

## Verification (definition of done)

1. **Translator coverage report.** Every reached code byte in every oracle run is
   covered by translated code, and there are zero dispatch misses.
2. **Lockstep differential test.** Run the recompiled program (Python backend, or
   gd2py of the GDScript) next to the Python oracle with the same key script and
   tick schedule. At every translated function entry and exit, or at least every
   N calls, compare registers, SP and a memory hash. Pass through boot,
   CONFIGURATION, team selection, roster load, a game in progress, and a complete
   `/s` season game with a **byte-identical `stats.log`**. Ticks must be
   delivered at the same logical points. For the comparison, drive the tick from
   a deterministic counter that both sides share; for example, count host polls
   or basic blocks in test mode.
3. **Instant replay.** `/i` reaches mode 4, and the CGA frames match the oracle.
4. **Speed.** Report the speed of the recompiled Python against the
   interpreter, as a proxy for the GDScript gain.
5. **GDScript checks.** All `.gd` files pass `gdparse`/`gdlint`, and that
   includes generated ones (lint may be relaxed for generated code). There is no
   interpreter in `godot/`: prove it with
   `grep -rn "match op\|fetch_byte\|decode" godot/`, or whatever the old
   interpreter's markers were.
6. **Docs.**
   - New `docs/RECOMPILATION.md`: design, the thread model, hints file format,
     limitations.
   - Update `README.md`, `godot/README.md` (new install step: run the
     recompiler; generated code is not committed) and `.gitignore`.
   - State honestly what was and was not executed.

## Working style

Run everything you claim. When the recompiled program diverges from the oracle,
bisect with the lockstep harness down to the first differing function or
instruction. Commit nothing, and report concisely at the end:
- what passed and what failed (with commands);
- coverage numbers;
- speed;
- what is untested in real Godot;
- any game-specific hints you had to add, and why.

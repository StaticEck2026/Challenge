#!/usr/bin/env bash
# One-command check of the Godot project with an official Godot 4.3 binary.
#
#   tools/godot_check.sh [--game-dir /path/to/nfl] [--godot /path/to/godot] [--no-smoke]
#
# 1. Downloads Godot 4.3-stable for this platform from the official
#    godotengine/godot-builds release into build/godot/ (once), and verifies it
#    against the release's SHA512-SUMS.txt.
# 2. If godot/recomp/ is missing and --game-dir is given, runs the recompiler
#    (python3 tools/recomp/recompile.py <game-dir> --bundle-data).
# 3. godot --headless --path godot --import       (imports, class cache)
# 4. godot --headless --path godot --script res://host/check_scripts.gd
#    (compiles every script, the generated ones included; reports failures)
# 5. the smoke mode for each scenario (boot, seasoncom, season):
#    godot --headless --path godot -- --smoke=<s> --smoke-out=build/godot/smoke
#    (runs the program on a deterministic key script on its own thread; prints
#    timing; screenshots in build/godot/smoke/; non-zero exit on any error,
#    dispatch miss or timeout)
#
# Linux (x86_64, arm64) and macOS run it directly; on Windows run it from
# Git Bash (or MSYS2).  Exit code 0 if every step passed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="4.3-stable"
BASE="https://github.com/godotengine/godot-builds/releases/download/${VER}"
OUT="${ROOT}/build/godot"
GAME=""
GODOT=""
SMOKE=1
TIMEOUT=900

while [ $# -gt 0 ]; do
  case "$1" in
    --game-dir) GAME="$2"; shift 2 ;;
    --godot) GODOT="$2"; shift 2 ;;
    --no-smoke) SMOKE=0; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT"

sha512() {
  if command -v sha512sum >/dev/null 2>&1; then sha512sum "$1" | cut -d' ' -f1
  else shasum -a 512 "$1" | cut -d' ' -f1; fi
}

if [ -z "$GODOT" ]; then
  case "$(uname -s)" in
    Linux)
      case "$(uname -m)" in
        x86_64|amd64) ZIP="Godot_v${VER}_linux.x86_64.zip"; BIN="Godot_v${VER}_linux.x86_64" ;;
        aarch64|arm64) ZIP="Godot_v${VER}_linux.arm64.zip"; BIN="Godot_v${VER}_linux.arm64" ;;
        *) echo "unsupported CPU $(uname -m); pass --godot" >&2; exit 2 ;;
      esac ;;
    Darwin) ZIP="Godot_v${VER}_macos.universal.zip"; BIN="Godot.app/Contents/MacOS/Godot" ;;
    MINGW*|MSYS*|CYGWIN*) ZIP="Godot_v${VER}_win64.exe.zip"; BIN="Godot_v${VER}_win64_console.exe" ;;
    *) echo "unsupported system $(uname -s); pass --godot" >&2; exit 2 ;;
  esac
  GODOT="${OUT}/${BIN}"
  if [ ! -x "$GODOT" ]; then
    echo "== downloading ${ZIP} and SHA512-SUMS.txt from ${BASE}"
    curl -fL --retry 3 -o "${OUT}/${ZIP}" "${BASE}/${ZIP}"
    curl -fL --retry 3 -o "${OUT}/SHA512-SUMS.txt" "${BASE}/SHA512-SUMS.txt"
    WANT="$(grep " ${ZIP}\$" "${OUT}/SHA512-SUMS.txt" | cut -d' ' -f1 || true)"
    GOT="$(sha512 "${OUT}/${ZIP}")"
    if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
      echo "SHA-512 MISMATCH for ${ZIP} (expected '${WANT}', got '${GOT}')" >&2
      rm -f "${OUT}/${ZIP}"
      exit 3
    fi
    echo "== SHA-512 verified: ${GOT:0:32}..."
    (cd "$OUT" && unzip -oq "$ZIP")
    chmod +x "$GODOT" 2>/dev/null || true
  fi
fi
echo "== Godot: $("$GODOT" --version 2>/dev/null | tail -n 1)"

if [ ! -f "${ROOT}/godot/recomp/recomp_index.gd" ]; then
  if [ -z "$GAME" ]; then
    echo "godot/recomp/ is missing: run  python3 tools/recomp/recompile.py /path/to/nfl --bundle-data" >&2
    echo "(or pass --game-dir /path/to/nfl)" >&2
    exit 2
  fi
  echo "== recompiling ${GAME}"
  python3 "${ROOT}/tools/recomp/recompile.py" "$GAME" --bundle-data
fi

FAIL=0
step() { echo; echo "== $1"; }

step "import (godot --headless --import)"
if ! "$GODOT" --headless --path "${ROOT}/godot" --import; then
  echo "(--import failed or is unknown; trying --editor --quit)"
  "$GODOT" --headless --path "${ROOT}/godot" --editor --quit || { echo "IMPORT FAILED"; FAIL=1; }
fi

step "compile every script"
if ! "$GODOT" --headless --path "${ROOT}/godot" --script res://host/check_scripts.gd; then
  echo "SCRIPT CHECK FAILED"; FAIL=1
fi

if [ "$SMOKE" = 1 ]; then
  DATA=()
  if [ -n "$GAME" ]; then DATA=("--dos-root=${GAME}"); fi
  rm -rf "${OUT}/smoke"
  mkdir -p "${OUT}/smoke"
  for s in boot seasoncom season; do
    step "smoke: ${s}"
    if ! "$GODOT" --headless --path "${ROOT}/godot" -- "--smoke=${s}" "--smoke-out=${OUT}/smoke" \
        "--smoke-timeout=${TIMEOUT}" ${DATA[@]+"${DATA[@]}"}; then
      echo "SMOKE ${s} FAILED"; FAIL=1
    fi
  done
  echo; echo "screenshots: ${OUT}/smoke/"
fi

echo
if [ "$FAIL" = 0 ]; then echo "== ALL GODOT CHECKS PASSED"; else echo "== SOME GODOT CHECKS FAILED"; fi
exit "$FAIL"

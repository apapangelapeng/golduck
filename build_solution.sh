#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./build_solution.sh [--force] [--no-visualize] SOURCE.c

Move SOURCE.c into solution/, compile the matching .wasm artifact, validate it,
and open the local visualizer with that solution selected.

Options:
  --force         Replace an existing solution source with the same name.
  --no-visualize  Build without starting or opening the visualizer.
  -h, --help      Show this help.

Set GOLDUCK_WASM_CLANG to override the clang executable.
EOF
}

force=0
visualize=1
source_arg=""

while (($#)); do
  case "$1" in
    --force)
      force=1
      ;;
    --no-visualize)
      visualize=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$source_arg" ]]; then
        echo "Only one C source may be supplied." >&2
        usage >&2
        exit 2
      fi
      source_arg="$1"
      ;;
  esac
  shift
done

if [[ -z "$source_arg" ]]; then
  usage >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
solution_dir="$script_dir/solution"

if [[ ! -f "$source_arg" ]]; then
  echo "C source not found: $source_arg" >&2
  exit 1
fi

source_dir="$(cd "$(dirname "$source_arg")" && pwd -P)"
source_name="$(basename "$source_arg")"
source_path="$source_dir/$source_name"

if [[ "$source_name" != *.c ]]; then
  echo "Source must have a .c extension: $source_name" >&2
  exit 1
fi

destination="$solution_dir/$source_name"
if [[ "$source_path" != "$destination" ]]; then
  if [[ -e "$destination" && "$force" -ne 1 ]]; then
    echo "Destination already exists: $destination (use --force to replace it)" >&2
    exit 1
  fi
  mv -f "$source_path" "$destination"
  echo "Moved $source_name to solution/."
fi

if [[ -n "${GOLDUCK_WASM_CLANG:-}" ]]; then
  wasm_clang="$GOLDUCK_WASM_CLANG"
elif [[ -x /opt/homebrew/opt/llvm/bin/clang ]]; then
  wasm_clang=/opt/homebrew/opt/llvm/bin/clang
elif command -v clang >/dev/null 2>&1; then
  wasm_clang="$(command -v clang)"
else
  echo "clang was not found; install an LLVM build with the wasm32 target." >&2
  exit 1
fi

stem="${source_name%.c}"
wasm_name="$stem.wasm"
wasm_path="$solution_dir/$wasm_name"
temporary_wasm="$(mktemp "$solution_dir/.$wasm_name.XXXXXX")"

cleanup() {
  if [[ -n "${temporary_wasm:-}" ]]; then
    rm -f "$temporary_wasm"
  fi
}
trap cleanup EXIT HUP INT TERM

"$wasm_clang" \
  --target=wasm32 \
  -O3 \
  -nostdlib \
  -fno-builtin \
  -Wl,--no-entry \
  -Wl,--export-memory \
  -Wl,--export=scratch_ptr \
  -Wl,--export=scratch_cap \
  -Wl,--allow-undefined \
  -Wl,--strip-all \
  -Wl,--export-dynamic \
  -I "$solution_dir" \
  "$destination" \
  -o "$temporary_wasm"

if command -v wasm-validate >/dev/null 2>&1; then
  wasm-validate "$temporary_wasm"
elif command -v wasm-tools >/dev/null 2>&1; then
  wasm-tools validate "$temporary_wasm"
else
  echo "Warning: no Wasm validator found; skipping structural validation." >&2
fi

if command -v wasm-objdump >/dev/null 2>&1; then
  wasm_details="$(wasm-objdump -x "$temporary_wasm")"
  for required_export in memory scratch_ptr scratch_cap run_entry; do
    if ! grep -Fq -- "-> \"$required_export\"" <<<"$wasm_details"; then
      echo "Wasm is missing required export: $required_export" >&2
      exit 1
    fi
  done
fi

mv -f "$temporary_wasm" "$wasm_path"
temporary_wasm=""
trap - EXIT HUP INT TERM
echo "Built $wasm_path"

if [[ "$visualize" -ne 1 ]]; then
  exit 0
fi

visualizer_url="http://127.0.0.1:8765/?solution=$wasm_name"
if ! curl -fsS --max-time 1 http://127.0.0.1:8765/api/solutions >/dev/null 2>&1; then
  if [[ -x "$script_dir/.venv/bin/python" ]]; then
    visualizer_python="$script_dir/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    visualizer_python="$(command -v python3)"
  else
    echo "Python 3 was not found; cannot start the visualizer." >&2
    exit 1
  fi

  visualizer_log="${TMPDIR:-/tmp}/golduck-visualizer.log"
  nohup "$visualizer_python" "$script_dir/visualize.py" \
    >"$visualizer_log" 2>&1 &
  visualizer_pid=$!

  visualizer_ready=0
  for _ in {1..50}; do
    if curl -fsS --max-time 1 http://127.0.0.1:8765/api/solutions >/dev/null 2>&1; then
      visualizer_ready=1
      break
    fi
    sleep 0.1
  done
  if [[ "$visualizer_ready" -ne 1 ]]; then
    echo "Visualizer failed to start; see $visualizer_log" >&2
    kill "$visualizer_pid" >/dev/null 2>&1 || true
    exit 1
  fi
fi

echo "Visualizer: $visualizer_url"
if [[ "$(uname -s)" == "Darwin" ]] && command -v open >/dev/null 2>&1; then
  open "$visualizer_url"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$visualizer_url" >/dev/null 2>&1 &
fi

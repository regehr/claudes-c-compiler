# YARPGEN Playbook

This playbook documents the workflow for finding and reducing compiler miscompiles using `yarpgen`, `clang`, `gcc`, `ccc`, and `cvise` in this repository.

Scope:
- Use `yarpgen` to generate C99 tests.
- Differentially test outputs across compilers.
- Reduce real disagreements with `cvise`.
- Preprocess testcase before any reduction step.
- Avoid false positives from obvious UB in reductions.

## Mandatory Bug-Fix Policy

For every confirmed `ccc` bug that gets fixed:
- Add a regression test in the compiler test suite as part of the same change.
- The test must fail with the pre-fix compiler and pass with the fix.
- A fix is not complete until the regression test is present and passing.

## Environment Baseline

- Repo root: `/home/regehr/claudes-c-compiler`
- `ccc` binary: `./target/release/ccc`
- `yarpgen` binary: `~/yarpgen/build/yarpgen`
- Reduction tool: `/usr/bin/cvise`

Recommended sanity checks:

```bash
rustup --version
cargo build --release
cargo test --release --lib --bins --tests
```

Use `--lib --bins --tests` to ignore doctest failures while keeping unit/integration tests green.

## 1) Generate One C99 Test and Triaging Manually

Generate:

```bash
~/yarpgen/build/yarpgen --std=c99
```

Expected outputs in current directory:
- `driver.c`
- `func.c`
- `init.h`

Compile all three compilers (ignore warnings from generated code):

```bash
clang -std=c99 -w driver.c func.c -o prog_clang
gcc   -std=c99 -w driver.c func.c -o prog_gcc
./target/release/ccc -std=c99 -w driver.c func.c -o prog_ccc
```

Run and compare:

```bash
./prog_clang > out_clang.txt
./prog_gcc   > out_gcc.txt
./prog_ccc   > out_ccc.txt
```

Interpretation:
- `clang == gcc == ccc`: no signal from this test.
- `clang == gcc != ccc`: likely `ccc` bug.
- `clang != gcc`: possible UB in test or toolchain/environment issue; investigate before filing.

## 2) Continuous Differential Testing

Use the existing loop driver in repo root:

```bash
python3 -u ./yarpgen_loop.py --progress-every 1
```

Behavior:
- Repeats until mismatch/manual stop, unless yarpgen itself fails.
- Generates C99 test with yarpgen each iteration.
- Builds with `clang/gcc/ccc` using `-w`.
- Compares `(return_code, stdout, stderr)`.
- Stops and preserves the failing case directory on mismatch.
- Compiler compile/runtime failures (including `ccc` crashes/ICEs/timeouts) are
  **non-interesting** for this workflow: the loop must skip them and continue.

Mandatory miscompile-only policy:
- Do not stop a campaign because `ccc` crashed or timed out.
- Keep running until a true output mismatch (`clang == gcc != ccc`) is found,
  or until the campaign limit is reached.
- If you need crash artifacts for a side investigation, use `--keep-skipped`;
  otherwise skipped cases should be deleted.

Mandatory campaign policy:
- Keep the top-level loop running until:
  - first bug/mismatch is found, or
  - `iter=10000` is reached with no bug.
- Do not stop earlier for convenience.

Practical tracking pattern:

```bash
python3 -u ./yarpgen_loop.py --progress-every 1 | tee yarpgen_loop.log
```

Stop only after a mismatch/fail message or after observing `[OK] iter=10000 ...`.
`[SKIP]` messages are expected and must not terminate the campaign.

By default, passing cases are deleted. Use `--keep-passing` only if you explicitly want all artifacts.

Recommended for reliable repeated runs:

```bash
RUN_ROOT="yarpgen_cases/run_$(date +%Y%m%d_%H%M%S)"
python3 -u ./yarpgen_loop.py --progress-every 1 --work-root "$RUN_ROOT"
```

Why:
- A fresh per-run directory keeps artifacts easy to inspect.
- It avoids confusion from old case numbering and stale artifacts.

If a run exits due compile timeout before finding a mismatch, rerun with a larger timeout:

```bash
python3 -u ./yarpgen_loop.py --progress-every 1 --compile-timeout 180
```

## 3) Start Reduction From a Mismatch Case

Assume failing case directory:

```text
yarpgen_cases/case_XXXXXXXX/
```

First verify mismatch is stable:

```bash
cd yarpgen_cases/case_XXXXXXXX
clang -std=c99 -w driver.c func.c -o prog_clang
./target/release/ccc -std=c99 -w driver.c func.c -o prog_ccc
./prog_clang > out_clang.txt
./prog_ccc   > out_ccc.txt
diff -u out_clang.txt out_ccc.txt
```

Localize which TU is miscompiled before reduction (often only `func.c`):

```bash
clang -std=c99 -w -O0 -c driver.c -o driver.clang.o
clang -std=c99 -w -O0 -c func.c   -o func.clang.o
./target/release/ccc -std=c99 -w -O0 -c driver.c -o driver.ccc.o
./target/release/ccc -std=c99 -w -O0 -c func.c   -o func.ccc.o
clang driver.clang.o func.ccc.o   -o mix_clangdriver_cccfunc
clang driver.ccc.o   func.clang.o -o mix_cccdriver_clangfunc
```

Use TU localization for diagnosis only. Do **not** use multi-TU reduction inputs.
Reduction must follow the single-file flow in Section 4.

## 4) MANDATORY: Merge To Single File And Preprocess Before Any Reduction

Non-negotiable policy (cannot be skipped):
- Always merge `driver.c` + `func.c` into one C input before reduction.
- Always preprocess that merged file before reduction.
- Any reduction run that skips either step is invalid; discard it and restart.

Never run `cvise` on raw yarpgen source and never run it on split TU inputs.
Always reduce `merged.pre.c`.

Required commands:

```bash
# Build a single TU while avoiding duplicate init.h inclusion.
# (yarpgen init.h may not be include-guarded.)
cp driver.c merged.c
sed '/^#include "init.h"$/d' func.c >> merged.c

clang -E -P -std=c99 -I . merged.c > merged.pre.c
```

Important compatibility fix for `ccc`:
- Preprocessed glibc content can contain:
  - `typedef float _Float32;`
  - `typedef double _Float64;`
  - `typedef double _Float32x;`
  - `typedef long double _Float64x;`
- `ccc` may treat `_Float*` as macros, producing invalid `typedef float float;`.

Fix:

```bash
sed -i \
  -e '/^typedef float _Float32;$/d' \
  -e '/^typedef double _Float64;$/d' \
  -e '/^typedef double _Float32x;$/d' \
  -e '/^typedef long double _Float64x;$/d' \
  merged.pre.c
```

Re-check mismatch after preprocessing. The exact numeric outputs may change after re-reduction; the signal is still `clang != ccc` (ideally `clang == gcc != ccc`).

## 5) C-Vise Reduction Workflow

Create a clean reduction directory and copy only required files.
Reduce only `merged.pre.c` (single-file, preprocessed input).

`interesting.sh` requirements:
- Uses local candidate filename only (no args, no absolute candidate path).
- Uses absolute paths only for tools.
- Compiles sanitized `clang`, plus `gcc`, plus `ccc`.
- Requires clean runtime stderr for all compared binaries.
- Requires `clang == gcc` and `clang != ccc`.
- Uses format-warning gate only (avoid `-Wstrict-prototypes` on yarpgen code).

Template:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/regehr/claudes-c-compiler"
CCC="$ROOT/target/release/ccc"
CAND="merged.pre.c"

rm -f prog_clang prog_gcc prog_ccc \
  out_clang.txt out_gcc.txt out_ccc.txt \
  err_clang.txt err_gcc.txt err_ccc.txt warn.log

timeout 30s clang -x c -std=c99 -fsyntax-only \
  -Wno-everything \
  -Wformat -Wformat-security -Wformat-extra-args \
  -Wformat-insufficient-args -Wformat-invalid-specifier \
  -Wformat-signedness \
  -Wincompatible-library-redeclaration \
  -Wdeprecated-non-prototype \
  -Werror=format -Werror=format-security -Werror=format-extra-args \
  -Werror=format-insufficient-args -Werror=format-invalid-specifier \
  -Werror=incompatible-library-redeclaration \
  -Werror=deprecated-non-prototype \
  "$CAND" > /dev/null 2> warn.log

timeout 30s clang -x c -std=c99 -w -O0 \
  -fsanitize=address,undefined -fno-sanitize-recover=all \
  "$CAND" -o prog_clang

timeout 30s gcc -x c -std=c99 -w -O0 \
  "$CAND" -o prog_gcc

timeout 30s "$CCC" -x c -std=c99 -w -O0 \
  "$CAND" -o prog_ccc

timeout 30s env ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ./prog_clang > out_clang.txt 2> err_clang.txt

timeout 30s ./prog_gcc > out_gcc.txt 2> err_gcc.txt

timeout 30s ./prog_ccc > out_ccc.txt 2> err_ccc.txt

test ! -s err_clang.txt
test ! -s err_gcc.txt
test ! -s err_ccc.txt
cmp -s out_clang.txt out_gcc.txt
! cmp -s out_clang.txt out_ccc.txt
```

Validate in-place and in a temp dir before invoking `cvise`:

```bash
chmod +x interesting.sh
./interesting.sh
REDUCE_DIR="$(pwd)"
DIR="$(mktemp -d)"
cp merged.pre.c "$DIR"
( cd "$DIR" && "$REDUCE_DIR/interesting.sh" )
rm -rf "$DIR"
```

Run:

```bash
cvise --n 8 --timeout 30 ./interesting.sh merged.pre.c
```

## 6) Validate Final Reduced Case

After reduction completes:

```bash
./interesting.sh
wc -l -c merged.pre.c
cat merged.pre.c
```

Also run a direct comparison:

```bash
clang -x c -std=c99 -w -O0 -fsanitize=address,undefined -fno-sanitize-recover=all \
  merged.pre.c -o final_clang
gcc -x c -std=c99 -w -O0 \
  merged.pre.c -o final_gcc
./target/release/ccc -x c -std=c99 -w -O0 \
  merged.pre.c -o final_ccc
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ./final_clang > final.out.clang 2> final.err.clang
./final_gcc > final.out.gcc 2> final.err.gcc
./final_ccc > final.out.ccc 2> final.err.ccc
diff -u final.out.clang final.out.gcc || true
diff -u final.out.clang final.out.ccc || true
```

## 7) Operational Notes for C-Vise

- `cvise` uses multiprocessing manager sockets.
- In restricted/sandboxed environments this can fail with:
  - `PermissionError: [Errno 1] Operation not permitted`
  - `EOFError` from multiprocessing manager startup.
- In that case, run `cvise` with elevated permissions.

Standing policy:
- Once `cvise` is running, let it run.
- Do not interrupt unless explicitly requested.

## 8) Common Failure Modes and Fixes

- `yarpgen` option failure:
  - Use `-d <dir>` for output directory.
  - `--out-dir <dir>` may fail on some builds; `--out-dir=<dir>` or `-d <dir>` is safer.

- Loop exits early on `ccc` compile timeout:
  - Cause: pathological generated case exceeds default compile timeout.
  - Fix: rerun with higher timeout, e.g. `--compile-timeout 180`.

- Older loop-driver versions can fail on reused case directories:
  - Symptom: `FileExistsError: ... case_XXXXXXXX`.
  - Fix: use a fresh `--work-root`, or update to the current hardened loop script.

- Background loop management confusion (stale pidfiles / missing process):
  - Do not trust a pidfile alone.
  - Always verify with:

  ```bash
  ps -p "$(cat yarpgen_loop.pid)" -o pid=,stat=,etime=,cmd=
  ```

  - If no process exists, remove stale pidfile and relaunch.
  - Prefer running in `tmux`/`screen` (or foreground) for long fuzzing sessions.

- Sanitized `clang` exits due LeakSanitizer:
  - Set `ASAN_OPTIONS=detect_leaks=0`.

- Preprocessed file fails in `ccc` due `_Float*` typedef expansion:
  - Remove the four `_Float*` typedef lines shown above.

- `cvise` says interestingness test does not return zero in temp dir:
  - Cause: script is not relocatable (uses wrong candidate path or assumes cwd layout).
  - Fix:
    - candidate must be referenced as local filename (use `merged.pre.c`),
    - validate both in-place and in a temp dir before `cvise`.

- Warning gate rejects original yarpgen inputs before reduction starts:
  - Cause: using broad warning errors (`-Wstrict-prototypes`, etc.) on noisy generated code.
  - Fix: gate only format/printf diagnostics using `-Wno-everything` plus explicit `-Wformat*` checks.

- Over-reduced testcase devolves into obvious UB (e.g., bad `printf` usage):
  - Enforce warning-gate checks in `interesting.sh` as above.

- Runtime sanitizers do not prove absence of all UB:
  - Sanitizers only check executed paths.
  - Dead-path UB may remain in reduced output; inspect reduced expressions manually when needed.

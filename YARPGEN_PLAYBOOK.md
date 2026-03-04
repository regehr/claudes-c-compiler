# YARPGEN Playbook

This playbook documents the workflow for finding and reducing compiler miscompiles using `yarpgen`, `clang`, `gcc`, `ccc`, and `cvise` in this repository.

Scope:
- Use `yarpgen` to generate C99 tests.
- Differentially test outputs across compilers.
- Reduce real disagreements with `cvise`.
- Preprocess testcase before any reduction step.
- Avoid false positives from obvious UB in reductions.
- For sanitizer gating, ALWAYS use BOTH ASan and UBSan together (never just one).
- For reduction-time UB screening, ALWAYS run a GCC UBSan runtime gate in addition to the clang ASan+UBSan gate.

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

## 2) Start Reduction From a Mismatch Case

Assume failing case directory:

```text
yarpgen_cases/case_XXXXXXXX/
```

First verify mismatch is stable:

```bash
cd yarpgen_cases/case_XXXXXXXX
ROOT="/home/regehr/claudes-c-compiler"
CCC="$ROOT/target/release/ccc"
clang -std=c99 -w driver.c func.c -o prog_clang
"$CCC" -std=c99 -w driver.c func.c -o prog_ccc
./prog_clang > out_clang.txt
./prog_ccc   > out_ccc.txt
diff -u out_clang.txt out_ccc.txt
```

Localize which TU is miscompiled before reduction (often only `func.c`):

```bash
clang -std=c99 -w -O0 -c driver.c -o driver.clang.o
clang -std=c99 -w -O0 -c func.c   -o func.clang.o
"$CCC" -std=c99 -w -O0 -c driver.c -o driver.ccc.o
"$CCC" -std=c99 -w -O0 -c func.c   -o func.ccc.o
clang driver.clang.o func.ccc.o   -o mix_clangdriver_cccfunc
clang driver.ccc.o   func.clang.o -o mix_cccdriver_clangfunc
```

Use TU localization for diagnosis only. Do **not** use multi-TU reduction inputs.
Reduction must follow the single-file flow in Section 3.

## 3) MANDATORY: Merge To Single File And Preprocess Before Any Reduction

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

## 4) C-Vise Reduction Workflow

Create a clean reduction directory and copy only required files.
Reduce only `merged.pre.c` (single-file, preprocessed input).

`interesting.sh` requirements:
- Uses local candidate filename only (no args, no absolute candidate path).
- Uses an absolute path for `ccc`; `clang/gcc/timeout/env` may come from `PATH`.
- Compiles sanitized `clang` with **ASan+UBSan** and no-recover, plus `gcc`, plus `ccc`.
- Compiles and runs an explicit **GCC UBSan** binary (`-fsanitize=undefined -fno-sanitize-recover=all`) as a mandatory UB gate.
  - Use relaxed-alignment mode for this gate: add `-fno-sanitize=alignment`.
- Requires clean runtime stderr for all compared binaries.
- Requires `clang == gcc` and `clang != ccc`.
- Uses a strict dual-compiler warning gate that includes:
  - format checks,
  - selected prototype/redeclaration checks, and
  - uninitialized local read checks (including aggregate/member paths).
- Avoids broad noisy gates such as `-Wstrict-prototypes`.

Mandatory sanitizer policy (cannot be skipped):
- **HARD REQUIREMENT (ZERO TOLERANCE): GCC UBSan runtime gating is mandatory for every reduction run.**
- `interesting.sh` must compile the clang baseline with
  `-fsanitize=address,undefined -fno-sanitize-recover=all`.
- Runtime must set:
  - `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`
  - `UBSAN_OPTIONS=halt_on_error=1`
- `interesting.sh` must also compile and run a GCC UBSan binary with:
  - compile: `-fsanitize=undefined -fno-sanitize=alignment -fno-sanitize-recover=all`
  - runtime: `UBSAN_OPTIONS=halt_on_error=1`
- The GCC UBSan run is mandatory and is part of interestingness.
- ALWAYS use **both** ASan and UBSan together for reduction and final validation.
- Never run with UBSan-only or ASan-only settings.
- Any reduction run that omits GCC UBSan, or does not enforce both clang sanitizers, is invalid.

Mandatory uninitialized-read policy (ZERO TOLERANCE, cannot be skipped):
- `interesting.sh` must enforce BOTH of the following gates:
  - Clang warning gate:
    - `-O2 -fsyntax-only`
    - `-Wuninitialized -Wconditional-uninitialized`
    - `-Werror=uninitialized -Werror=conditional-uninitialized`
  - GCC warning gate:
    - `-O2 -c` (not `-fsyntax-only`)
    - `-Wuninitialized -Wmaybe-uninitialized`
    - `-Werror=uninitialized -Werror=maybe-uninitialized`
- `interesting.sh` must also run `clang --analyze` and reject
  `core.uninitialized.*` findings.
- `interesting.sh` must also run a GCC analyzer gate and reject uninitialized-use findings:
  - `-fanalyzer -Wanalyzer-use-of-uninitialized-value`
  - enforce with `-Werror=analyzer-use-of-uninitialized-value`
- Any reduction run that omits any one of these checks is invalid and must be discarded.
- Rationale: Clang warning diagnostics alone can miss uninitialized reads through
  struct/array members; GCC analyzer backstop is required.

Mandatory pointer-vs-integer comparison policy (ZERO TOLERANCE, cannot be skipped):
- `interesting.sh` must run dedicated warning scans with BOTH compilers:
  - Clang scan: `-O2 -fsyntax-only -Wall`
  - GCC scan: `-O2 -c -Wall`
- `interesting.sh` must reject the testcase if either scan reports pointer-vs-integer
  comparisons (for example, diagnostics containing:
  - `ordered comparison between pointer and integer`
  - `comparison between pointer and integer`)
- Any reduction run that does not enforce this rejection rule is invalid and must be discarded.
- This is a hard UB guardrail: do not "explain away" these warnings after reduction.

Template:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/regehr/claudes-c-compiler"
CCC="$ROOT/target/release/ccc"
CAND="merged.pre.c"

rm -f prog_clang prog_gcc prog_ccc \
  prog_gcc_ubsan \
  out_clang.txt out_gcc.txt out_ccc.txt \
  err_clang.txt err_gcc.txt err_ccc.txt err_gcc_ubsan.txt \
  warn_clang.log warn_gcc.log warn_analyze.log warn_gcc_analyze.log \
  warn_gcc.o warn_gcc_analyze.o \
  warn_ptrint_clang.log warn_ptrint_gcc.log warn_ptrint_gcc.o

timeout 30s clang -x c -std=c99 -O2 -fsyntax-only \
  -Wno-everything \
  -Wformat -Wformat-security -Wformat-extra-args \
  -Wformat-insufficient-args -Wformat-invalid-specifier \
  -Wformat-signedness \
  -Wuninitialized -Wconditional-uninitialized \
  -Wincompatible-library-redeclaration \
  -Wdeprecated-non-prototype \
  -Werror=format \
  -Werror=uninitialized -Werror=conditional-uninitialized \
  -Werror=incompatible-library-redeclaration \
  -Werror=deprecated-non-prototype \
  "$CAND" > warn_clang.log 2>&1 || exit 1

timeout 30s gcc -x c -std=c99 -O2 -c \
  -Wuninitialized -Wmaybe-uninitialized \
  -Werror=uninitialized -Werror=maybe-uninitialized \
  "$CAND" -o warn_gcc.o > warn_gcc.log 2>&1 || exit 1

timeout 45s gcc -x c -std=c99 -O0 -fanalyzer -c \
  -Wanalyzer-use-of-uninitialized-value \
  -Werror=analyzer-use-of-uninitialized-value \
  "$CAND" -o warn_gcc_analyze.o > warn_gcc_analyze.log 2>&1 || exit 1

timeout 30s clang -x c -std=c99 -O2 -fsyntax-only \
  -Wall "$CAND" > warn_ptrint_clang.log 2>&1 || exit 1

timeout 30s gcc -x c -std=c99 -O2 -c \
  -Wall "$CAND" -o warn_ptrint_gcc.o > warn_ptrint_gcc.log 2>&1 || exit 1

if rg -qi \
  "ordered comparison between pointer and integer|comparison between pointer and integer" \
  warn_ptrint_clang.log warn_ptrint_gcc.log; then
  exit 1
fi

timeout 30s clang -x c -std=c99 -O0 --analyze \
  "$CAND" > warn_analyze.log 2>&1 || exit 1
if rg -q "core\\.uninitialized\\." warn_analyze.log; then
  exit 1
fi

timeout 30s clang -x c -std=c99 -w -O0 \
  -fsanitize=address,undefined -fno-sanitize-recover=all \
  "$CAND" -o prog_clang > /dev/null 2>err_clang.txt || exit 1

timeout 30s gcc -x c -std=c99 -w \
  "$CAND" -o prog_gcc > /dev/null 2>err_gcc.txt || exit 1

timeout 30s gcc -x c -std=c99 -w -O0 \
  -fsanitize=undefined -fno-sanitize=alignment -fno-sanitize-recover=all \
  "$CAND" -o prog_gcc_ubsan > /dev/null 2>err_gcc_ubsan.txt || exit 1

timeout 30s "$CCC" -x c -std=c99 -w \
  "$CAND" -o prog_ccc > /dev/null 2>err_ccc.txt || exit 1

timeout 5s env ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ./prog_clang > out_clang.txt 2>>err_clang.txt || exit 1

timeout 5s env UBSAN_OPTIONS=halt_on_error=1 \
  ./prog_gcc_ubsan > /dev/null 2>>err_gcc_ubsan.txt || exit 1

timeout 5s ./prog_gcc > out_gcc.txt 2>>err_gcc.txt || exit 1

timeout 5s ./prog_ccc > out_ccc.txt 2>>err_ccc.txt || exit 1

[ ! -s err_clang.txt ] || exit 1
[ ! -s err_gcc.txt ] || exit 1
[ ! -s err_gcc_ubsan.txt ] || exit 1
[ ! -s err_ccc.txt ] || exit 1
cmp -s out_clang.txt out_gcc.txt || exit 1
! cmp -s out_clang.txt out_ccc.txt || exit 1
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

## 5) Validate Final Reduced Case

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
gcc -x c -std=c99 -w -O0 -fsanitize=undefined -fno-sanitize=alignment -fno-sanitize-recover=all \
  merged.pre.c -o final_gcc_ubsan
ROOT="/home/regehr/claudes-c-compiler"
CCC="$ROOT/target/release/ccc"
"$CCC" -x c -std=c99 -w -O0 \
  merged.pre.c -o final_ccc
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ./final_clang > final.out.clang 2> final.err.clang
UBSAN_OPTIONS=halt_on_error=1 \
  ./final_gcc_ubsan > /dev/null 2> final.err.gcc_ubsan
./final_gcc > final.out.gcc 2> final.err.gcc
./final_ccc > final.out.ccc 2> final.err.ccc
diff -u final.out.clang final.out.gcc || true
diff -u final.out.clang final.out.ccc || true
```

Final validation rule (cannot be skipped):
- The final clang comparison build/run must keep BOTH sanitizers enabled:
  `-fsanitize=address,undefined -fno-sanitize-recover=all`,
  `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`,
  `UBSAN_OPTIONS=halt_on_error=1`.
- The final validation must also include a GCC UBSan run with:
  `-fsanitize=undefined -fno-sanitize=alignment -fno-sanitize-recover=all`,
  `UBSAN_OPTIONS=halt_on_error=1`.
- Any UBSan stderr from the GCC UBSan run invalidates the testcase.
- A "final" testcase validated without clang ASan+UBSan **and** GCC UBSan is invalid.

Mandatory reporting rule (cannot be skipped):
- In the user-facing final report, always show the full final reduced testcase
  content (`merged.pre.c`), not just size/path/output summaries.

## 6) Operational Notes for C-Vise

- `cvise` uses multiprocessing manager sockets.
- In restricted/sandboxed environments this can fail with:
  - `PermissionError: [Errno 1] Operation not permitted`
  - `EOFError` from multiprocessing manager startup.
- In that case, run `cvise` with elevated permissions.

Standing policy:
- Once `cvise` is running, let it run.
- Do not interrupt unless explicitly requested.

## 7) Common Failure Modes and Fixes

- `yarpgen` option failure:
  - Use `-d <dir>` for output directory.
  - `--out-dir <dir>` may fail on some builds; `--out-dir=<dir>` or `-d <dir>` is safer.

- Sanitized `clang` exits due LeakSanitizer:
  - Keep BOTH sanitizers enabled.
  - Set `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1` and keep
    `UBSAN_OPTIONS=halt_on_error=1`.

- Preprocessed file fails in `ccc` due `_Float*` typedef expansion:
  - Remove the four `_Float*` typedef lines shown above.

- `cvise` says interestingness test does not return zero in temp dir:
  - Cause: script is not relocatable (uses wrong candidate path or assumes cwd layout).
  - Fix:
    - candidate must be referenced as local filename (use `merged.pre.c`),
    - validate both in-place and in a temp dir before `cvise`.

- Warning gate rejects original yarpgen inputs before reduction starts:
  - Cause: using broad warning errors (`-Wstrict-prototypes`, etc.) on noisy generated code.
  - Fix: use `-Wno-everything` plus explicit checks for format diagnostics,
    selected redeclaration/prototype diagnostics, and uninitialized local reads
    (`-O2`, `-Wuninitialized`, `-Wconditional-uninitialized`).

- Reduced testcase still contains uninitialized automatic local reads:
  - Cause: only compiler warning checks were enforced; those missed this path.
  - Fix: enforce the mandatory dual gate and analyzer backstop:
    - Clang: `-O2 -fsyntax-only -Wuninitialized -Wconditional-uninitialized`
      with corresponding `-Werror=` flags.
    - GCC: `-O2 -c -Wuninitialized -Wmaybe-uninitialized`
      with corresponding `-Werror=` flags.
    - Clang analyzer: reject `core.uninitialized.*` from `clang --analyze`.
    - GCC analyzer: enforce
      `-fanalyzer -Wanalyzer-use-of-uninitialized-value -Werror=analyzer-use-of-uninitialized-value`.
  - Absolute rule: if any of these are missing, the reduction is invalid.

- Reduced testcase still contains pointer-vs-integer comparisons:
  - Cause: interestingness gate did not enforce compiler warning rejection for this UB class.
  - Fix: enforce the mandatory dual-compiler pointer-vs-integer scan and rejection rule.
  - Absolute rule: if either compiler reports pointer-vs-integer comparison diagnostics,
    the testcase is invalid for bug-fixing and reduction must continue.

- Reduced testcase hits UB that clang sanitizers do not report (for example, zero-length-array OOB patterns):
  - Cause: relying on clang sanitizer runtime only.
  - Fix: enforce mandatory GCC UBSan runtime gate in `interesting.sh` and final validation.
  - Absolute rule: a reduction without GCC UBSan runtime screening is invalid.

- Over-reduced testcase devolves into obvious UB (e.g., bad `printf` usage):
  - Enforce warning-gate checks in `interesting.sh` as above.

- Runtime sanitizers do not prove absence of all UB:
  - Sanitizers only check executed paths.
  - Dead-path UB may remain in reduced output; inspect reduced expressions manually when needed.

## Bugs Fixed

- https://github.com/regehr/claudes-c-compiler/commit/4d9913e7f53be66e6de30869e1a324020ce81777
- https://github.com/regehr/claudes-c-compiler/commit/32fe7f5e5fe08bb0b7bf3ee7e6bb90234356d29e
- https://github.com/regehr/claudes-c-compiler/commit/abeb8fbdce8c6f2c99557cf148efc9483b9c902a
- https://github.com/regehr/claudes-c-compiler/commit/00fbea89eb855a359eea6c2c976b0c2f2fbecd1e
- https://github.com/regehr/claudes-c-compiler/commit/90905856a09bba6ab4df4aade850342078db7850
- https://github.com/regehr/claudes-c-compiler/commit/c01bac0f988471855c5422cafe5d3d57e5ed2e58
- https://github.com/regehr/claudes-c-compiler/commit/5b0447eabf19163c90484415d7a292df1781af66
- https://github.com/regehr/claudes-c-compiler/commit/b1c97854ffa7b9d3d5f53f93f0a089ca0b56f0f6
- https://github.com/regehr/claudes-c-compiler/commit/acc1b4a5f9618d7e7d9c7e917afe7b622caf346a
- https://github.com/regehr/claudes-c-compiler/commit/ceff82eba63c2b9290370e48fac850a7a709d8f9
- https://github.com/regehr/claudes-c-compiler/commit/9fe29b62241e3e08a82bbe61d752fc0660a6526c

    

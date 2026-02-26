# YARPGEN Playbook

This playbook documents the workflow for finding and reducing compiler miscompiles using `yarpgen`, `clang`, `gcc`, `ccc`, and `cvise` in this repository.

Scope:
- Use `yarpgen` to generate C99 tests.
- Differentially test outputs across compilers.
- Reduce real disagreements with `cvise`.
- Avoid false positives from obvious UB in reductions.

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
./yarpgen_loop.py
```

Behavior:
- Repeats forever.
- Generates C99 test with yarpgen each iteration.
- Builds with `clang/gcc/ccc` using `-w`.
- Compares `(return_code, stdout, stderr)`.
- Stops and preserves the failing case directory on mismatch.

By default, passing cases are deleted. Use `--keep-passing` only if you explicitly want all artifacts.

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

## 4) Build a Single-File Reproducer Before Reduction

Single-file reduction is usually easier than multi-file reduction.

Create merged source:

```bash
{ cat driver.c; tail -n +2 func.c; } > merged.c
```

Why `tail -n +2 func.c`:
- `func.c` starts with `#include "init.h"`.
- `driver.c` already includes `init.h`.
- This avoids duplicate include lines in the merged file.

Re-verify mismatch on `merged.c`:

```bash
clang -std=c99 -w -O0 -fsanitize=address,undefined -fno-sanitize-recover=all merged.c -o merged_clang_san
./target/release/ccc -std=c99 -w -O0 merged.c -o merged_ccc
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./merged_clang_san > out_clang.txt 2>err_clang.txt
./merged_ccc > out_ccc.txt 2>err_ccc.txt
```

Note:
- `ASAN_OPTIONS=detect_leaks=0` is needed in this environment because LeakSanitizer can fail under ptrace-like constraints.

## 5) Preprocess Safely

Preprocess:

```bash
clang -E -P -std=c99 merged.c > merged.pre.c
```

Important compatibility fix for `ccc`:
- Preprocessed glibc content can contain:
  - `typedef float _Float32;`
  - `typedef double _Float64;`
  - `typedef double _Float32x;`
  - `typedef long double _Float64x;`
- `ccc` may treat `_Float*` as macros, causing invalid expansion like `typedef float float;`.

Practical fix:

```bash
sed -i \
  -e '/^typedef float _Float32;$/d' \
  -e '/^typedef double _Float64;$/d' \
  -e '/^typedef double _Float32x;$/d' \
  -e '/^typedef long double _Float64x;$/d' \
  merged.pre.c
```

Then re-check mismatch exactly as above on `merged.pre.c`.

## 6) C-Vise Reduction Workflow

Create a clean reduction directory and copy only necessary files:

```bash
mkdir -p yarpgen_cases/reduce_target
cp merged.pre.c yarpgen_cases/reduce_target/
```

Create `interesting.sh` in reduction directory with these properties:
- Compile with sanitized `clang`.
- Compile with `ccc`.
- Run both.
- Require clean stderr.
- Require output mismatch.
- Reject bad reductions with `printf`-related UB/warnings.

Template:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/regehr/claudes-c-compiler"
CCC="$ROOT/target/release/ccc"
SRC="merged.pre.c"

rm -f prog_clang prog_ccc out_clang.txt out_ccc.txt err_clang.txt err_ccc.txt warn.log

# Reject printf-format and printf-declaration problems during reduction.
timeout 20s clang -x c -std=c99 -fsyntax-only \
  -Werror=incompatible-library-redeclaration \
  -Werror=format \
  -Werror=format-security \
  -Werror=format-extra-args \
  -Werror=format-insufficient-args \
  -Werror=format-invalid-specifier \
  "$SRC" > /dev/null 2> warn.log

timeout 20s clang -x c -std=c99 -w -O0 \
  -fsanitize=address,undefined -fno-sanitize-recover=all \
  "$SRC" -o prog_clang

timeout 20s "$CCC" -x c -std=c99 -w -O0 "$SRC" -o prog_ccc

timeout 20s env ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  ./prog_clang > out_clang.txt 2> err_clang.txt

timeout 20s ./prog_ccc > out_ccc.txt 2> err_ccc.txt

test ! -s err_clang.txt
test ! -s err_ccc.txt
! cmp -s out_clang.txt out_ccc.txt
```

Make executable and validate before reduction:

```bash
chmod +x interesting.sh
./interesting.sh
```

If exit code is `0`, the testcase is interesting.

Run `cvise`:

```bash
cvise --n 8 --timeout 30 ./interesting.sh merged.pre.c
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

## 8) Validate Final Reduced Case

After reduction completes:

```bash
./interesting.sh
cat merged.pre.c
wc -l -c merged.pre.c
```

Also run a direct comparison one more time:

```bash
clang -x c -std=c99 -w -O0 -fsanitize=address,undefined -fno-sanitize-recover=all merged.pre.c -o final_clang
./target/release/ccc -x c -std=c99 -w -O0 merged.pre.c -o final_ccc
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./final_clang > final.out.clang 2> final.err.clang
./final_ccc > final.out.ccc 2> final.err.ccc
diff -u final.out.clang final.out.ccc || true
```

## 9) Common Failure Modes and Fixes

- `yarpgen` option failure:
  - Use `-d <dir>` for output directory.
  - `--out-dir <dir>` may fail on some builds; `--out-dir=<dir>` or `-d <dir>` is safer.

- Sanitized `clang` exits due LeakSanitizer:
  - Set `ASAN_OPTIONS=detect_leaks=0`.

- Preprocessed file fails in `ccc` due `_Float*` typedef expansion:
  - Remove the four `_Float*` typedef lines shown above.

- Over-reduced testcase devolves into obvious UB (e.g., bad `printf` usage):
  - Enforce warning-gate checks in `interesting.sh` as above.


#!/usr/bin/env python3
"""Infinite yarpgen differential tester for clang/gcc/ccc."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run_cmd(
    cmd: list[str],
    cwd: Path,
    timeout: float,
    merge_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def parse_seed(yarpgen_stdout: str) -> str:
    for line in yarpgen_stdout.splitlines():
        line = line.strip()
        if "SEED" in line:
            return line
    return "<unknown-seed>"


def short_text(s: str, max_len: int = 240) -> str:
    s = s.replace("\n", "\\n")
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def compiler_cmd(cmd_str: str) -> list[str]:
    parts = shlex.split(cmd_str)
    if not parts:
        raise ValueError("Compiler command cannot be empty")
    return parts


def resolve_cmd_path(cmd: list[str], base_dir: Path) -> list[str]:
    if not cmd:
        return cmd
    exe = cmd[0]
    if "/" not in exe:
        return cmd
    exe_path = Path(exe).expanduser()
    if not exe_path.is_absolute():
        candidate = (base_dir / exe_path).resolve()
        if candidate.exists():
            cmd = cmd.copy()
            cmd[0] = str(candidate)
    else:
        cmd = cmd.copy()
        cmd[0] = str(exe_path)
    return cmd


def write_text_file(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[WARN] Failed to write {path}: {exc}", file=sys.stderr)


def allocate_case_dir(work_root: Path, start_iteration: int) -> tuple[int, Path]:
    iteration = start_iteration
    while True:
        iteration += 1
        case_dir = work_root / f"case_{iteration:08d}"
        try:
            case_dir.mkdir(parents=True, exist_ok=False)
            return iteration, case_dir
        except FileExistsError:
            # Another run may already be using this id; skip and keep going.
            continue


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate C99 programs with yarpgen and compare clang/gcc/ccc outputs forever."
    )
    parser.add_argument(
        "--yarpgen",
        default="~/yarpgen/build/yarpgen",
        help="Path to yarpgen binary",
    )
    parser.add_argument(
        "--clang",
        default="clang",
        help="Clang command (can include extra fixed args)",
    )
    parser.add_argument(
        "--gcc",
        default="gcc",
        help="GCC command (can include extra fixed args)",
    )
    parser.add_argument(
        "--ccc",
        default="./target/release/ccc",
        help="ccc command (can include extra fixed args)",
    )
    parser.add_argument(
        "--work-root",
        default="./yarpgen_cases",
        help="Directory used to store case artifacts",
    )
    parser.add_argument(
        "--compile-timeout",
        type=float,
        default=60.0,
        help="Per-compiler compile timeout in seconds",
    )
    parser.add_argument(
        "--run-timeout",
        type=float,
        default=10.0,
        help="Per-executable runtime timeout in seconds",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1,
        help="Print progress every N successful iterations",
    )
    parser.add_argument(
        "--keep-passing",
        action="store_true",
        help="Keep artifacts for passing iterations (default: delete them)",
    )
    args = parser.parse_args()
    if args.progress_every <= 0:
        print("ERROR: --progress-every must be >= 1", file=sys.stderr)
        return 2

    root = Path.cwd()
    yarpgen = Path(args.yarpgen).expanduser()
    work_root = Path(args.work_root).expanduser()
    if not work_root.is_absolute():
        work_root = root / work_root
    work_root.mkdir(parents=True, exist_ok=True)

    clang_cmd = resolve_cmd_path(compiler_cmd(args.clang), root)
    gcc_cmd = resolve_cmd_path(compiler_cmd(args.gcc), root)
    ccc_cmd = resolve_cmd_path(compiler_cmd(args.ccc), root)

    if not yarpgen.exists():
        print(f"ERROR: yarpgen not found at {yarpgen}", file=sys.stderr)
        return 2

    print(f"Using yarpgen: {yarpgen}")
    print(f"Using clang:   {' '.join(clang_cmd)}")
    print(f"Using gcc:     {' '.join(gcc_cmd)}")
    print(f"Using ccc:     {' '.join(ccc_cmd)}")
    print(f"Artifacts dir: {work_root}")
    print("Starting infinite differential loop. Press Ctrl-C to stop.")

    iteration = 0
    start_time = time.time()

    try:
        while True:
            iteration, case_dir = allocate_case_dir(work_root, iteration)

            # 1) Generate C99 test with yarpgen.
            try:
                gen = run_cmd(
                    [str(yarpgen), "--std=c99", "-d", str(case_dir)],
                    cwd=root,
                    timeout=args.compile_timeout,
                    merge_stderr=True,
                )
            except subprocess.TimeoutExpired:
                print(f"[FAIL] Iteration {iteration}: yarpgen timed out")
                print(f"Case kept at: {case_dir}")
                return 1
            except OSError as exc:
                print(f"[FAIL] Iteration {iteration}: yarpgen launch failed: {exc}")
                print(f"Case kept at: {case_dir}")
                return 1

            seed = parse_seed(gen.stdout)
            write_text_file(case_dir / "yarpgen.log", gen.stdout)

            if gen.returncode != 0:
                print(f"[FAIL] Iteration {iteration}: yarpgen failed ({seed})")
                print(f"Case kept at: {case_dir}")
                return 1

            # 2) Compile with clang/gcc/ccc using -w.
            compilers = [
                ("clang", clang_cmd, "prog_clang"),
                ("gcc", gcc_cmd, "prog_gcc"),
                ("ccc", ccc_cmd, "prog_ccc"),
            ]

            for name, cmd, out_name in compilers:
                full_cmd = cmd + ["-std=c99", "-w", "driver.c", "func.c", "-o", out_name]
                try:
                    cp = run_cmd(full_cmd, cwd=case_dir, timeout=args.compile_timeout)
                except subprocess.TimeoutExpired:
                    print(f"[FAIL] Iteration {iteration}: {name} compile timeout ({seed})")
                    print(f"Case kept at: {case_dir}")
                    return 1
                except OSError as exc:
                    print(f"[FAIL] Iteration {iteration}: {name} compile launch failed ({seed})")
                    print(f"Case kept at: {case_dir}")
                    print(f"{name} launch error: {exc}")
                    return 1

                write_text_file(case_dir / f"compile_{name}.stdout", cp.stdout)
                write_text_file(case_dir / f"compile_{name}.stderr", cp.stderr)

                if cp.returncode != 0:
                    print(f"[FAIL] Iteration {iteration}: {name} compile failed ({seed})")
                    print(f"Case kept at: {case_dir}")
                    print(f"{name} stderr: {short_text(cp.stderr)}")
                    return 1

            # 3) Run all three executables and compare outputs.
            results: dict[str, tuple[int, str, str]] = {}
            for name, _, exe in compilers:
                try:
                    rp = run_cmd([f"./{exe}"], cwd=case_dir, timeout=args.run_timeout)
                except subprocess.TimeoutExpired:
                    print(f"[FAIL] Iteration {iteration}: {name} runtime timeout ({seed})")
                    print(f"Case kept at: {case_dir}")
                    return 1
                except OSError as exc:
                    print(f"[FAIL] Iteration {iteration}: {name} runtime launch failed ({seed})")
                    print(f"Case kept at: {case_dir}")
                    print(f"{name} launch error: {exc}")
                    return 1

                results[name] = (rp.returncode, rp.stdout, rp.stderr)
                write_text_file(case_dir / f"run_{name}.stdout", rp.stdout)
                write_text_file(case_dir / f"run_{name}.stderr", rp.stderr)

            baseline = results["clang"]
            mismatch = (
                results["gcc"] != baseline
                or results["ccc"] != baseline
            )
            if mismatch:
                print(f"[MISMATCH] Iteration {iteration}: output disagreement ({seed})")
                print(f"Case kept at: {case_dir}")
                for name in ("clang", "gcc", "ccc"):
                    rc, out, err = results[name]
                    print(
                        f"{name}: rc={rc}, stdout={short_text(out)!r}, stderr={short_text(err)!r}"
                    )
                return 1

            if iteration % args.progress_every == 0:
                elapsed = time.time() - start_time
                out_hash = hashlib.sha256(baseline[1].encode("utf-8")).hexdigest()[:16]
                print(
                    f"[OK] iter={iteration} elapsed={elapsed:.1f}s seed={seed} stdout_sha256={out_hash}"
                )

            if not args.keep_passing:
                shutil.rmtree(case_dir, ignore_errors=True)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Infinite parallel yarpgen differential tester for clang/gcc/ccc."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def case_path(work_root: Path, iteration: int) -> Path:
    return work_root / f"case_{iteration:08d}"


def make_run_root(work_root: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base_name = f"run_{stamp}_{os.getpid()}"
    run_root = work_root / base_name
    suffix = 1
    while run_root.exists():
        run_root = work_root / f"{base_name}_{suffix:02d}"
        suffix += 1
    run_root.mkdir(parents=True, exist_ok=False)
    return run_root


@dataclass(frozen=True)
class WorkerConfig:
    root: str
    work_root: str
    yarpgen: str
    clang_cmd: list[str]
    gcc_cmd: list[str]
    ccc_cmd: list[str]
    compile_timeout: float
    run_timeout: float
    keep_passing: bool
    keep_skipped: bool


def run_iteration(cfg: WorkerConfig, iteration: int) -> dict[str, Any]:
    root = Path(cfg.root)
    work_root = Path(cfg.work_root)
    case_dir = case_path(work_root, iteration)
    try:
        case_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {"status": "collision", "iteration": iteration}

    def skip(reason: str, seed: str, detail: str | None = None) -> dict[str, Any]:
        if not cfg.keep_skipped:
            shutil.rmtree(case_dir, ignore_errors=True)
        return {
            "status": "skip",
            "iteration": iteration,
            "seed": seed,
            "case_dir": str(case_dir),
            "case_kept": cfg.keep_skipped,
            "reason": reason,
            "detail": detail,
        }

    def fail(reason: str, seed: str = "<unknown-seed>", detail: str | None = None) -> dict[str, Any]:
        return {
            "status": "fail",
            "iteration": iteration,
            "seed": seed,
            "case_dir": str(case_dir),
            "reason": reason,
            "detail": detail,
        }

    try:
        gen = run_cmd(
            [cfg.yarpgen, "--std=c", "-o", str(case_dir)],
            cwd=root,
            timeout=cfg.compile_timeout,
            merge_stderr=True,
        )
    except subprocess.TimeoutExpired:
        return skip("yarpgen timed out", seed="<unknown-seed>")
    except OSError as exc:
        return fail("yarpgen launch failed", detail=f"yarpgen launch error: {exc}")

    write_text_file(case_dir / "yarpgen.log", gen.stdout)
    seed = parse_seed(gen.stdout)
    if gen.returncode != 0:
        return fail("yarpgen failed", seed=seed)

    compilers = [
        ("clang", cfg.clang_cmd, "prog_clang"),
        ("gcc", cfg.gcc_cmd, "prog_gcc"),
        ("ccc", cfg.ccc_cmd, "prog_ccc"),
    ]

    for name, cmd, out_name in compilers:
        full_cmd = cmd + ["-std=c99", "-w", "driver.c", "func.c", "-o", out_name]
        try:
            cp = run_cmd(full_cmd, cwd=case_dir, timeout=cfg.compile_timeout)
        except subprocess.TimeoutExpired:
            return skip(f"{name} compile timeout", seed=seed)
        except OSError as exc:
            return skip(
                f"{name} compile launch failed",
                seed=seed,
                detail=f"{name} launch error: {exc}",
            )

        cp_stdout = cp.stdout or ""
        cp_stderr = cp.stderr or ""
        write_text_file(case_dir / f"compile_{name}.stdout", cp_stdout)
        write_text_file(case_dir / f"compile_{name}.stderr", cp_stderr)

        if cp.returncode != 0:
            return skip(
                f"{name} compile failed",
                seed=seed,
            )

    results: dict[str, tuple[int, str, str]] = {}
    for name, _, exe in compilers:
        try:
            rp = run_cmd([f"./{exe}"], cwd=case_dir, timeout=cfg.run_timeout)
        except subprocess.TimeoutExpired:
            return skip(f"{name} runtime timeout", seed=seed)
        except OSError as exc:
            return skip(
                f"{name} runtime launch failed",
                seed=seed,
                detail=f"{name} launch error: {exc}",
            )

        rp_stdout = rp.stdout or ""
        rp_stderr = rp.stderr or ""
        results[name] = (rp.returncode, rp_stdout, rp_stderr)
        write_text_file(case_dir / f"run_{name}.stdout", rp_stdout)
        write_text_file(case_dir / f"run_{name}.stderr", rp_stderr)

        if rp.returncode != 0:
            return skip(
                f"{name} runtime failed",
                seed=seed,
                detail=(
                    f"{name} exited with rc={rp.returncode}, "
                    f"stderr: {short_text(rp_stderr)}"
                ),
            )

    baseline = results["clang"]
    mismatch = results["gcc"] != baseline or results["ccc"] != baseline
    if mismatch:
        summaries: dict[str, tuple[int, str, str]] = {}
        for name in ("clang", "gcc", "ccc"):
            rc, out, err = results[name]
            summaries[name] = (rc, short_text(out), short_text(err))
        return {
            "status": "mismatch",
            "iteration": iteration,
            "seed": seed,
            "case_dir": str(case_dir),
            "summaries": summaries,
        }

    out_hash = hashlib.sha256(baseline[1].encode("utf-8")).hexdigest()[:16]
    if not cfg.keep_passing:
        shutil.rmtree(case_dir, ignore_errors=True)

    return {
        "status": "ok",
        "iteration": iteration,
        "seed": seed,
        "stdout_hash": out_hash,
    }


def submit_iteration(
    executor: concurrent.futures.Executor,
    futures: dict[concurrent.futures.Future[dict[str, Any]], int],
    cfg: WorkerConfig,
    iteration: int,
) -> int:
    next_iteration = iteration + 1
    fut = executor.submit(run_iteration, cfg, next_iteration)
    futures[fut] = next_iteration
    return next_iteration


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
        help="Parent directory used to store run directories and case artifacts",
    )
    parser.add_argument(
        "--compile-timeout",
        type=float,
        default=120.0,
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
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="Number of concurrent test cases (default: all cores)",
    )
    parser.add_argument(
        "--keep-passing",
        action="store_true",
        help="Keep artifacts for passing iterations (default: delete them)",
    )
    parser.add_argument(
        "--keep-skipped",
        action="store_true",
        help="Keep artifacts for skipped iterations (compile/runtime failures)",
    )
    args = parser.parse_args()
    if args.progress_every <= 0:
        print("ERROR: --progress-every must be >= 1", file=sys.stderr)
        return 2
    if args.jobs <= 0:
        print("ERROR: --jobs must be >= 1", file=sys.stderr)
        return 2

    root = Path.cwd()
    yarpgen = Path(args.yarpgen).expanduser()
    work_root = Path(args.work_root).expanduser()
    if not work_root.is_absolute():
        work_root = root / work_root
    work_root.mkdir(parents=True, exist_ok=True)
    run_root = make_run_root(work_root)

    clang_cmd = resolve_cmd_path(compiler_cmd(args.clang), root)
    gcc_cmd = resolve_cmd_path(compiler_cmd(args.gcc), root)
    ccc_cmd = resolve_cmd_path(compiler_cmd(args.ccc), root)

    if not yarpgen.exists():
        print(f"ERROR: yarpgen not found at {yarpgen}", file=sys.stderr)
        return 2

    cfg = WorkerConfig(
        root=str(root),
        work_root=str(run_root),
        yarpgen=str(yarpgen),
        clang_cmd=clang_cmd,
        gcc_cmd=gcc_cmd,
        ccc_cmd=ccc_cmd,
        compile_timeout=args.compile_timeout,
        run_timeout=args.run_timeout,
        keep_passing=args.keep_passing,
        keep_skipped=args.keep_skipped,
    )

    print(f"Using yarpgen: {yarpgen}")
    print(f"Using clang:   {' '.join(clang_cmd)}")
    print(f"Using gcc:     {' '.join(gcc_cmd)}")
    print(f"Using ccc:     {' '.join(ccc_cmd)}")
    print(f"Parallel jobs: {args.jobs}")
    print(f"Artifacts dir: {run_root}")
    print("Starting infinite differential loop. Press Ctrl-C to stop.")

    iteration = 0
    ok_cases = 0
    skipped_cases = 0
    start_time = time.time()

    executor: concurrent.futures.Executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=args.jobs
    )
    futures: dict[concurrent.futures.Future[dict[str, Any]], int] = {}

    for _ in range(args.jobs):
        iteration = submit_iteration(executor, futures, cfg, iteration)

    try:
        while True:
            done, _ = concurrent.futures.wait(
                list(futures.keys()),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                assigned_iteration = futures.pop(fut)
                try:
                    result = fut.result()
                except Exception as exc:
                    print(f"[FAIL] Iteration {assigned_iteration}: worker crashed: {exc}")
                    return 1

                status = str(result.get("status", ""))
                if status == "collision":
                    iteration = submit_iteration(executor, futures, cfg, iteration)
                    continue

                result_iteration = int(result.get("iteration", assigned_iteration))
                seed = str(result.get("seed", "<unknown-seed>"))

                if status == "ok":
                    ok_cases += 1
                    if ok_cases % args.progress_every == 0:
                        elapsed = time.time() - start_time
                        out_hash = str(result.get("stdout_hash", "<unknown-hash>"))
                        print(
                            f"[OK] iter={result_iteration} ok={ok_cases} skipped={skipped_cases} "
                            f"elapsed={elapsed:.1f}s seed={seed} stdout_sha256={out_hash}"
                        )
                    iteration = submit_iteration(executor, futures, cfg, iteration)
                    continue

                if status == "skip":
                    skipped_cases += 1
                    reason = str(result.get("reason", "unknown skip"))
                    print(f"[SKIP] Iteration {result_iteration}: {reason} ({seed})")
                    detail = result.get("detail")
                    if detail:
                        print(str(detail))
                    if bool(result.get("case_kept", False)):
                        print(f"Case kept at: {result['case_dir']}")
                    iteration = submit_iteration(executor, futures, cfg, iteration)
                    continue

                if status == "mismatch":
                    print(f"[MISMATCH] Iteration {result_iteration}: output disagreement ({seed})")
                    print(f"Case kept at: {result['case_dir']}")
                    summaries = result.get("summaries", {})
                    if isinstance(summaries, dict):
                        for name in ("clang", "gcc", "ccc"):
                            value = summaries.get(name)
                            if isinstance(value, tuple) and len(value) == 3:
                                rc, out, err = value
                                print(
                                    f"{name}: rc={rc}, stdout={out!r}, stderr={err!r}"
                                )
                    return 1

                if status == "fail":
                    reason = str(result.get("reason", "unknown failure"))
                    print(f"[FAIL] Iteration {result_iteration}: {reason}")
                    detail = result.get("detail")
                    if detail:
                        print(str(detail))
                    print(f"Case kept at: {result['case_dir']}")
                    return 1

                print(f"[FAIL] Iteration {result_iteration}: unknown worker status {status!r}")
                if "case_dir" in result:
                    print(f"Case kept at: {result['case_dir']}")
                return 1

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 0
    finally:
        for fut in list(futures.keys()):
            fut.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    raise SystemExit(main())

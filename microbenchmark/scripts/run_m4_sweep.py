#!/usr/bin/env python3
"""Run the RP2350 Arm kernel set on gem5's single-core Cortex-M4 model."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SUMMARY_FIELDS = [
    "timestamp_utc", "category", "benchmark", "board", "core", "sys_hz",
    "reps", "min_cycles", "mean_cycles", "median_cycles", "max_cycles",
    "pstdev_cycles", "duration_s", "uf2_sha256",
]
REP_FIELDS = [
    "timestamp_utc", "category", "benchmark", "board", "core", "sys_hz",
    "rep", "cycles",
]
ERROR_FIELDS = ["timestamp_utc", "category", "benchmark", "attempt", "error"]
STEADY_FIELDS = [
    "category", "benchmark", "board", "core", "sys_hz", "steady_reps",
    "steady_min_cycles", "steady_mean_cycles", "steady_median_cycles",
    "steady_max_cycles", "steady_pstdev_cycles", "rep0_cycles",
    "rep0_minus_steady_median_cycles",
]


@dataclass(frozen=True)
class Kernel:
    category: str
    name: str
    elf: Path


@dataclass(frozen=True)
class Result:
    kernel: Kernel
    cycles: list[int]
    elapsed: float
    sha256: str
    run_dir: Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stats_windows(text: str) -> list[str]:
    windows: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines(keepends=True):
        if "Begin Simulation Statistics" in line:
            current = []
        elif "End Simulation Statistics" in line:
            if current is not None:
                windows.append("".join(current))
                current = None
        elif current is not None:
            current.append(line)
    return windows


def validate_single_core(config: str) -> None:
    cpu_sections = re.findall(r"^\[system\.cpu(?:\d+)?\]$", config, re.MULTILINE)
    if cpu_sections != ["[system.cpu]"]:
        raise RuntimeError(f"expected exactly [system.cpu], found {cpu_sections!r}")
    cpu_body_match = re.search(
        r"^\[system\.cpu\]\n(.*?)(?=^\[)", config, re.MULTILINE | re.DOTALL
    )
    if not cpu_body_match:
        raise RuntimeError("missing [system.cpu] body")
    cpu_body = cpu_body_match.group(1)
    for required in ("cpu_id=0", "numThreads=1", "type=BaseMinorCPU"):
        if required not in cpu_body:
            raise RuntimeError(f"single-core evidence missing {required!r}")


def run_one(kernel: Kernel, args: argparse.Namespace) -> Result:
    run_dir = args.output_dir / "logs" / kernel.category / kernel.name
    run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.gem5_bin), "-re", "-d", str(run_dir), str(args.runner),
        "--firmware", str(kernel.elf), "--run-to-exit",
    ]
    started = time.monotonic()
    proc = subprocess.run(
        command,
        cwd=args.gem5_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=args.timeout_s,
    )
    elapsed = time.monotonic() - started
    (run_dir / "launcher.log").write_text(
        "$ " + " ".join(command) + "\n" + proc.stdout
    )
    if proc.returncode:
        raise RuntimeError(f"gem5 exited {proc.returncode}: {proc.stdout[-2000:]}")

    simout = (run_dir / "simout.txt").read_text(errors="replace")
    if simout.count("*** ROI BEGIN") != args.reps:
        raise RuntimeError(
            f"expected {args.reps} ROI BEGIN events, found "
            f"{simout.count('*** ROI BEGIN')}"
        )
    if simout.count("*** ROI END") != args.reps:
        raise RuntimeError(
            f"expected {args.reps} ROI END events, found "
            f"{simout.count('*** ROI END')}"
        )
    if f"MICROBENCH name={kernel.name}" not in simout:
        raise RuntimeError("firmware completion marker missing")
    if "because m5_exit instruction encountered" not in simout:
        raise RuntimeError("clean m5_exit termination missing")

    validate_single_core((run_dir / "config.ini").read_text(errors="replace"))
    windows = stats_windows((run_dir / "stats.txt").read_text(errors="replace"))
    # gem5 writes one final process-exit block after the ten ROI dumps.
    if len(windows) != args.reps + 1:
        raise RuntimeError(
            f"expected {args.reps} ROI windows plus one exit block, found {len(windows)}"
        )
    values: list[int] = []
    for rep, window in enumerate(windows[: args.reps]):
        match = re.search(r"^system\.cpu\.numCycles\s+(\d+)", window, re.MULTILINE)
        if not match:
            raise RuntimeError(f"rep {rep}: system.cpu.numCycles missing")
        values.append(int(match.group(1)))
    return Result(kernel, values, elapsed, file_sha256(kernel.elf), run_dir)


def append_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    create = not path.exists() or not path.stat().st_size
    with path.open("a", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if create:
            writer.writeheader()
        writer.writerows(rows)
        stream.flush()


def ensure_csv(path: Path, fields: list[str]) -> None:
    if path.exists() and path.stat().st_size:
        return
    with path.open("w", newline="") as stream:
        csv.DictWriter(stream, fieldnames=fields).writeheader()


def save_result(output: Path, result: Result) -> None:
    timestamp = now()
    values = result.cycles
    common = {
        "timestamp_utc": timestamp,
        "category": result.kernel.category,
        "benchmark": result.kernel.name,
        "board": "gem5-stm32g474re",
        "core": "cortex-m4",
        "sys_hz": 170_000_000,
    }
    append_rows(output / "summary.csv", SUMMARY_FIELDS, [{
        **common,
        "reps": len(values),
        "min_cycles": min(values),
        "mean_cycles": f"{statistics.fmean(values):.3f}",
        "median_cycles": f"{statistics.median(values):.3f}",
        "max_cycles": max(values),
        "pstdev_cycles": f"{statistics.pstdev(values):.3f}",
        "duration_s": f"{result.elapsed:.3f}",
        # Same physical schema; this is the ELF digest (documented in manifest).
        "uf2_sha256": result.sha256,
    }])
    append_rows(output / "repetitions.csv", REP_FIELDS, [
        {**common, "rep": rep, "cycles": cycles}
        for rep, cycles in enumerate(values)
    ])


def completed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="") as stream:
        return {row["benchmark"] for row in csv.DictReader(stream)}


def write_steady(output: Path) -> None:
    grouped: dict[str, list[dict[str, str]]] = {}
    with (output / "repetitions.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            grouped.setdefault(row["benchmark"], []).append(row)
    rows = []
    for benchmark in sorted(grouped):
        samples = sorted(grouped[benchmark], key=lambda row: int(row["rep"]))
        rep0 = int(samples[0]["cycles"])
        steady = [int(row["cycles"]) for row in samples if int(row["rep"]) > 0]
        median = statistics.median(steady)
        rows.append({
            "category": samples[0]["category"],
            "benchmark": benchmark,
            "board": samples[0]["board"],
            "core": samples[0]["core"],
            "sys_hz": samples[0]["sys_hz"],
            "steady_reps": len(steady),
            "steady_min_cycles": min(steady),
            "steady_mean_cycles": f"{statistics.fmean(steady):.3f}",
            "steady_median_cycles": f"{median:.3f}",
            "steady_max_cycles": max(steady),
            "steady_pstdev_cycles": f"{statistics.pstdev(steady):.3f}",
            "rep0_cycles": rep0,
            "rep0_minus_steady_median_cycles": f"{rep0 - median:.3f}",
        })
    with (output / "steady_state_summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=STEADY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gem5-bin", type=Path, required=True)
    parser.add_argument("--gem5-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--firmware-dir", type=Path, required=True)
    parser.add_argument("--kernel-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--reps", type=int, default=10)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ensure_csv(args.output_dir / "summary.csv", SUMMARY_FIELDS)
    ensure_csv(args.output_dir / "repetitions.csv", REP_FIELDS)
    ensure_csv(args.output_dir / "errors.csv", ERROR_FIELDS)

    kernels = []
    for category in ("alu", "fp"):
        for source in sorted((args.kernel_root / category).glob("bench_*.S")):
            kernels.append(Kernel(category, source.stem, args.firmware_dir / f"{source.stem}.elf"))
    if len(kernels) != 184:
        raise SystemExit(f"expected 184 kernels, found {len(kernels)}")
    missing = [str(k.elf) for k in kernels if not k.elf.exists()]
    if missing:
        raise SystemExit("missing firmware:\n" + "\n".join(missing))

    done = completed(args.output_dir / "summary.csv")
    pending = [kernel for kernel in kernels if kernel.name not in done]
    print(f"Cortex-M4: {len(done)} complete, {len(pending)} pending; jobs={args.jobs}", flush=True)
    failures: list[tuple[Kernel, str]] = []
    count = len(done)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, kernel, args): kernel for kernel in pending}
        for future in as_completed(futures):
            kernel = futures[future]
            try:
                result = future.result()
                save_result(args.output_dir, result)
                count += 1
                print(
                    f"[{count:03d}/184] {kernel.category}/{kernel.name}: "
                    f"median={statistics.median(result.cycles):g}", flush=True
                )
            except Exception as exc:
                message = str(exc)
                failures.append((kernel, message))
                append_rows(args.output_dir / "errors.csv", ERROR_FIELDS, [{
                    "timestamp_utc": now(), "category": kernel.category,
                    "benchmark": kernel.name, "attempt": 1, "error": message,
                }])
                print(f"FAILED {kernel.category}/{kernel.name}: {message}", flush=True)

    write_steady(args.output_dir)
    git_commit = subprocess.run(
        ["git", "-C", str(args.kernel_root.parent.parent), "rev-parse", "HEAD"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout.strip()
    gem5_commit = subprocess.run(
        ["git", "-C", str(args.gem5_root), "rev-parse", "HEAD"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout.strip()
    gem5_build_info = subprocess.run(
        [str(args.gem5_bin), "--build-info"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout.strip()
    manifest = {
        "updated_utc": now(),
        "architecture": "arm",
        "simulator": "gem5",
        "model": "STM32G474RETimingBoard/CortexM4CPU (BaseMinorCPU)",
        "single_core": True,
        "expected_core_id": 0,
        "active_core_count": 1,
        "modeled_clock_hz": 170_000_000,
        "selected_kernel_count": len(kernels),
        "completed_kernel_count": count,
        "failed_kernel_count": len(failures),
        "inner_reps": args.reps,
        "cycle_stat": "system.cpu.numCycles",
        "post_exit_stats_window_excluded": True,
        "sha256_field_note": "uf2_sha256 contains the gem5 ELF SHA-256 for schema compatibility",
        "repository_commit": git_commit,
        "gem5_repository_commit": gem5_commit,
        "gem5_build_info": gem5_build_info,
        "gem5_binary": str(args.gem5_bin.resolve()),
        "gem5_runner": str(args.runner.resolve()),
        "output_dir": str(args.output_dir),
        "host_parallel_jobs": args.jobs,
        "kernels": [{"category": k.category, "name": k.name} for k in kernels],
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output_dir / "single_core_evidence.txt").write_text(
        "architecture=arm\nmodel=gem5-cortex-m4-minor\nexpected_cpu_sections=[system.cpu]\n"
        "expected_cpu_id=0\nexpected_numThreads=1\n"
        f"configs_verified={count}\nconfig_violations=0\n"
    )
    print(f"Finished: completed={count} failed={len(failures)} output={args.output_dir}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

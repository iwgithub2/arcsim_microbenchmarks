#!/usr/bin/env python3
"""Build, flash, and collect every RP2350 Arm or Hazard3 microbenchmark.

The sweep is intentionally crash-safe: each completed benchmark is appended to
CSV immediately, raw build/flash/serial output is kept per kernel, and rerunning
with the same --output-dir automatically skips successful rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from run_benchmark import CaptureResult, capture_available_port, flash_uf2


ROOT = Path(__file__).resolve().parent
KERNEL_ROOT = ROOT.parent / "kernels"
SUMMARY_FIELDS = [
    "timestamp_utc", "category", "benchmark", "board", "core", "sys_hz",
    "reps", "min_cycles", "mean_cycles", "median_cycles", "max_cycles",
    "pstdev_cycles", "duration_s", "uf2_sha256",
]
REP_FIELDS = [
    "timestamp_utc", "category", "benchmark", "board", "core", "sys_hz",
    "rep", "cycles",
]
ERROR_FIELDS = [
    "timestamp_utc", "category", "benchmark", "attempt", "error",
]
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
    source: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discover_kernels(architecture: str) -> list[Kernel]:
    kernels = []
    if architecture == "riscv":
        sources = sorted(KERNEL_ROOT.glob("riscv/alu/bench_*.S"))
    else:
        sources = sorted(KERNEL_ROOT.glob("alu/bench_*.S"))
        sources += sorted(KERNEL_ROOT.glob("fp/bench_*.S"))
    for source in sources:
        kernels.append(Kernel(source.parent.name, source.stem, source))
    return kernels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Physical single-core RP2350 sweep with resumable CSV collection."
    )
    parser.add_argument(
        "--architecture", choices=("arm", "riscv"), default="arm",
        help="Run Cortex-M33 Arm kernels or Hazard3 RISC-V equivalents",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="Run directory; reuse the same path to resume",
    )
    parser.add_argument(
        "--preset",
        help="CMake preset (selected from --architecture by default)",
    )
    parser.add_argument("--port", help="USB CDC path (auto-detected by default)")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--category", action="append", choices=("alu", "fp"),
        help="Limit to one or more categories",
    )
    parser.add_argument(
        "--bench", action="append",
        help="Limit to one or more exact benchmark names",
    )
    parser.add_argument("--start-at", help="Skip names before this benchmark")
    parser.add_argument("--limit", type=int, help="Run at most this many")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def command_output(cmd: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout}"
        )
    return proc.stdout.strip()


def configure_and_build(
    kernel: Kernel, preset: str, build_dir: Path
) -> tuple[Path, str]:
    configure_log = command_output([
        "cmake", "--preset", preset, f"-DRP2350_BENCH={kernel.name}",
    ])
    build_log = command_output([
        "cmake", "--build", "--preset", preset,
        "--target", "rp2350_benchmark",
    ])
    uf2 = build_dir / "rp2350_benchmark.uf2"
    if not uf2.exists():
        raise RuntimeError(f"build succeeded but UF2 is missing: {uf2}")
    return uf2, f"$ cmake configure\n{configure_log}\n\n$ cmake build\n{build_log}\n"


def ensure_csv(path: Path, fields: list[str]) -> None:
    if path.exists() and path.stat().st_size:
        return
    with path.open("w", newline="") as stream:
        csv.DictWriter(stream, fieldnames=fields).writeheader()


def append_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("a", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())


def completed_benchmarks(summary_path: Path) -> set[str]:
    if not summary_path.exists():
        return set()
    with summary_path.open(newline="") as stream:
        return {row["benchmark"] for row in csv.DictReader(stream)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(
    path: Path,
    *, args: argparse.Namespace,
    selected: list[Kernel],
    completed: int,
    failed: int,
) -> None:
    def version(cmd: list[str]) -> str:
        try:
            return command_output(cmd).splitlines()[0]
        except Exception as exc:  # metadata must never abort data collection
            return f"unavailable: {exc}"

    data = {
        "updated_utc": utc_now(),
        "host": platform.platform(),
        "preset": args.preset,
        "architecture": args.architecture,
        "single_core": True,
        "expected_core_id": 0,
        "active_core_count": 1,
        "output_dir": str(args.output_dir.resolve()),
        "selected_kernel_count": len(selected),
        "completed_kernel_count": completed,
        "failed_kernel_count": failed,
        "inner_reps": 10,
        "pico_sdk": version([
            "git", "-C", str(ROOT / "../../../pico-sdk"), "describe",
            "--tags", "--always",
        ]),
        "compiler": version([
            str(
                ROOT / "../../../toolchains/riscv-toolchain-16/bin/"
                "riscv32-pico-elf-gcc"
                if args.architecture == "riscv"
                else ROOT / "../../../toolchains/gcc-arm-embedded/Payload/bin/"
                "arm-none-eabi-gcc"
            ),
            "--version",
        ]),
        "picotool": version(["picotool", "version"]),
        "repository_commit": version([
            "git", "-C", str(ROOT / "../.."), "rev-parse", "HEAD",
        ]),
        "kernels": [
            {"category": kernel.category, "name": kernel.name}
            for kernel in selected
        ],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def write_single_core_evidence(
    output_path: Path, logs_path: Path, build_dir: Path, architecture: str
) -> None:
    expected_core = "hazard3" if architecture == "riscv" else "cortex-m33"
    expected = (
        f"core={expected_core} core_id=0 active_cores=1 "
        f"implementation={'native-riscv' if architecture == 'riscv' else 'native-arm'}"
    )
    runtime_headers: list[str] = []
    for log_path in sorted(logs_path.glob("*/*.log")):
        for line in log_path.read_text(errors="replace").splitlines():
            if line.startswith("MICROBENCH_BOARD "):
                runtime_headers.append(line)
                break
    invalid = [line for line in runtime_headers if expected not in line]
    if invalid:
        raise RuntimeError(
            f"{len(invalid)} runtime header(s) violate the single-core invariant"
        )
    link_proof = build_dir / "rp2350_benchmark.single-core.txt"
    if not link_proof.exists():
        raise RuntimeError(f"missing link-time single-core proof: {link_proof}")
    output_path.write_text(
        f"architecture={architecture}\n"
        f"expected_core={expected_core}\n"
        "expected_core_id=0\n"
        "expected_active_cores=1\n"
        f"runtime_headers_verified={len(runtime_headers)}\n"
        "runtime_header_violations=0\n"
        + link_proof.read_text()
    )


def save_success(
    *,
    kernel: Kernel,
    result: CaptureResult,
    elapsed: float,
    firmware_hash: str,
    summary_path: Path,
    reps_path: Path,
) -> None:
    timestamp = utc_now()
    values = [cycles for _, cycles in result.reps]
    append_csv(summary_path, SUMMARY_FIELDS, [{
        "timestamp_utc": timestamp,
        "category": kernel.category,
        "benchmark": kernel.name,
        "board": result.board,
        "core": result.core,
        "sys_hz": result.sys_hz,
        "reps": len(values),
        "min_cycles": min(values),
        "mean_cycles": f"{statistics.fmean(values):.3f}",
        "median_cycles": f"{statistics.median(values):.3f}",
        "max_cycles": max(values),
        "pstdev_cycles": f"{statistics.pstdev(values):.3f}",
        "duration_s": f"{elapsed:.3f}",
        "uf2_sha256": firmware_hash,
    }])
    append_csv(reps_path, REP_FIELDS, [{
        "timestamp_utc": timestamp,
        "category": kernel.category,
        "benchmark": kernel.name,
        "board": result.board,
        "core": result.core,
        "sys_hz": result.sys_hz,
        "rep": rep,
        "cycles": cycles,
    } for rep, cycles in result.reps])


def write_steady_state_summary(reps_path: Path, output_path: Path) -> None:
    """Summarize reps 1..N-1 while retaining rep 0's cold penalty."""
    grouped: dict[str, list[dict[str, str]]] = {}
    with reps_path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            grouped.setdefault(row["benchmark"], []).append(row)

    rows: list[dict[str, object]] = []
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

    with output_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=STEADY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if args.preset is None:
        args.preset = (
            "mac-pico2-riscv-sweep"
            if args.architecture == "riscv"
            else "mac-pico2-sweep"
        )
    if args.output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.output_dir = ROOT / "results" / f"rp2350-{args.architecture}-{stamp}"
    build_dir = ROOT / (
        "build-riscv-sweep" if args.architecture == "riscv" else "build-sweep"
    )
    kernels = discover_kernels(args.architecture)
    available_count = 56 if args.architecture == "riscv" else 184
    if len(kernels) != available_count:
        raise SystemExit(
            f"expected {available_count} {args.architecture} kernels, "
            f"found {len(kernels)}"
        )

    if args.architecture == "riscv":
        if args.category and "fp" in args.category:
            raise SystemExit(
                "RP2350 Hazard3 has no floating-point unit; "
                "RISC-V sweeps intentionally exclude the 128 FP kernels"
            )
    if args.category:
        kernels = [k for k in kernels if k.category in set(args.category)]
    if args.bench:
        requested = set(args.bench)
        known = {k.name for k in kernels}
        missing = sorted(requested - known)
        if missing:
            raise SystemExit(f"unknown benchmark(s): {', '.join(missing)}")
        kernels = [k for k in kernels if k.name in requested]
    if args.start_at:
        names = [k.name for k in kernels]
        if args.start_at not in names:
            raise SystemExit(f"--start-at benchmark not selected: {args.start_at}")
        kernels = kernels[names.index(args.start_at):]
    if args.limit is not None:
        kernels = kernels[:args.limit]

    print(
        f"Selected {len(kernels)} of {available_count} RP2350 "
        f"{args.architecture} kernels."
    )
    if args.dry_run:
        for kernel in kernels:
            print(f"{kernel.category},{kernel.name}")
        return 0

    output = args.output_dir.resolve()
    logs = output / "logs"
    output.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    summary_path = output / "summary.csv"
    reps_path = output / "repetitions.csv"
    errors_path = output / "errors.csv"
    manifest_path = output / "manifest.json"
    ensure_csv(summary_path, SUMMARY_FIELDS)
    ensure_csv(reps_path, REP_FIELDS)
    ensure_csv(errors_path, ERROR_FIELDS)

    already_done = completed_benchmarks(summary_path)
    pending = [kernel for kernel in kernels if kernel.name not in already_done]
    if already_done:
        print(f"Resuming: {len(already_done)} successful benchmark(s) already recorded.")
    print(f"Output: {output}")

    failures = 0
    completed_this_run = 0
    total = len(pending)
    for index, kernel in enumerate(pending, 1):
        print(f"[{index:03d}/{total:03d}] {kernel.category}/{kernel.name}", flush=True)
        log_dir = logs / kernel.category
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{kernel.name}.log"
        error_messages: list[str] = []

        for attempt in range(1, args.retries + 2):
            started = time.monotonic()
            try:
                uf2, build_log = configure_and_build(
                    kernel, args.preset, build_dir
                )
                firmware_hash = sha256(uf2)
                flash_log = flash_uf2(uf2, update=True)
                deadline = time.monotonic() + args.timeout_s
                port, result = capture_available_port(
                    kernel.name, deadline, args.port
                )
                expected_core = (
                    "hazard3" if args.architecture == "riscv" else "cortex-m33"
                )
                if result.core != expected_core:
                    raise RuntimeError(
                        f"expected {expected_core}, firmware reported {result.core}"
                    )
                if result.core_id != 0 or result.active_cores != 1:
                    raise RuntimeError(
                        "single-core invariant failed: "
                        f"core_id={result.core_id}, active_cores={result.active_cores}"
                    )
                expected_indices = list(range(result.expected_reps))
                if [rep for rep, _ in result.reps] != expected_indices:
                    raise RuntimeError(
                        f"non-contiguous repetitions: {result.reps}"
                    )
                elapsed = time.monotonic() - started
                log_path.write_text(
                    f"benchmark={kernel.name}\ncategory={kernel.category}\n"
                    f"attempt={attempt}\nport={port}\n\n"
                    f"{build_log}\n$ picotool load\n{flash_log}\n"
                    f"$ usb serial\n{result.raw_text}"
                )
                save_success(
                    kernel=kernel,
                    result=result,
                    elapsed=elapsed,
                    firmware_hash=firmware_hash,
                    summary_path=summary_path,
                    reps_path=reps_path,
                )
                values = [cycles for _, cycles in result.reps]
                print(
                    f"  ok: {result.sys_hz} Hz, min={min(values)}, "
                    f"mean={statistics.fmean(values):.1f}, max={max(values)}, "
                    f"{elapsed:.1f}s",
                    flush=True,
                )
                completed_this_run += 1
                break
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                error_messages.append(f"attempt {attempt}: {message}")
                append_csv(errors_path, ERROR_FIELDS, [{
                    "timestamp_utc": utc_now(),
                    "category": kernel.category,
                    "benchmark": kernel.name,
                    "attempt": attempt,
                    "error": message,
                }])
                print(f"  attempt {attempt} failed: {message}", flush=True)
                if attempt <= args.retries:
                    time.sleep(0.5)
        else:
            failures += 1
            log_path.write_text("\n\n".join(error_messages) + "\n")

        write_manifest(
            manifest_path,
            args=args,
            selected=kernels,
            completed=len(already_done) + completed_this_run,
            failed=failures,
        )

    print(
        f"Sweep complete: {completed_this_run} newly completed, "
        f"{len(already_done)} resumed, {failures} failed."
    )
    write_manifest(
        manifest_path,
        args=args,
        selected=kernels,
        completed=len(already_done) + completed_this_run,
        failed=failures,
    )
    steady_path = output / "steady_state_summary.csv"
    write_steady_state_summary(reps_path, steady_path)
    evidence_path = output / "single_core_evidence.txt"
    write_single_core_evidence(
        evidence_path, logs, build_dir, args.architecture
    )
    print(f"Summary: {summary_path}")
    print(f"Steady state: {steady_path}")
    print(f"Repetitions: {reps_path}")
    print(f"Single-core evidence: {evidence_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

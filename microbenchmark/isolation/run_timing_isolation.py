#!/usr/bin/env python3
"""Run only new residual-gap isolation experiments and preserve every artifact."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


CHANGES = (
    "fetch_limit1", "fetch2_decode2", "decode_execute2",
    "two_fetch_stage",
)
SLOPE_CASES = (
    ("conditional_taken", "forward_recovered"),
    ("call_indirect", "baseline"),
    ("jump_indirect", "jump_icache2k"),
)
TRACE_CASES = (
    ("forward_taken", "forward_recovered"),
    ("call_indirect", "baseline"),
    ("jump_indirect", "jump_icache2k"),
)
NOP_SIZES = (0, 1, 2, 4, 8, 16, 32, 64, 100)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_output(command: list[str]) -> str:
    return subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ).stdout.strip()


def run_one(task, args):
    group, name, profile, change, benchmark, trace = task
    run_dir = args.output / "runs" / group / name
    run_dir.mkdir(parents=True)
    elf = (args.firmware_dir / f"{benchmark}.elf").resolve()
    command = [
        str(args.gem5_bin), "-re", "-d", str(run_dir),
        "--debug-file=minortrace.log", str(args.runner),
        "--firmware", str(elf), "--profile", profile, "--change", change,
    ]
    if trace:
        command.append("--trace")
    started = time.monotonic()
    proc = subprocess.run(
        command, cwd=args.gem5_root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=args.timeout,
    )
    elapsed = time.monotonic() - started
    (run_dir / "launcher.log").write_text(
        "$ " + " ".join(command) + "\n" + proc.stdout
    )
    if proc.returncode:
        raise RuntimeError(f"{group}/{name}: exit {proc.returncode}")
    simout = (run_dir / "simout.txt").read_text(errors="replace")
    stats = (run_dir / "stats.txt").read_text(errors="replace")
    if simout.count("*** ROI BEGIN") != 10 or simout.count("*** ROI END") != 10:
        raise RuntimeError(f"{group}/{name}: expected 10 complete ROIs")
    if stats.count("Begin Simulation Statistics") != 11:
        raise RuntimeError(f"{group}/{name}: expected 10 ROI + exit stats")
    if "because m5_exit instruction encountered" not in simout:
        raise RuntimeError(f"{group}/{name}: missing clean m5_exit")
    trace_path = run_dir / "minortrace.log"
    if trace and (not trace_path.is_file() or not trace_path.stat().st_size):
        raise RuntimeError(f"{group}/{name}: missing MinorTrace output")
    return {
        "group": group, "name": name, "profile": profile, "change": change,
        "benchmark": benchmark, "trace": trace, "elf": str(elf),
        "elf_sha256": sha256(elf), "elapsed_s": round(elapsed, 3),
        "command": command,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gem5-bin", type=Path, required=True)
    parser.add_argument("--gem5-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--firmware-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.gem5_bin = args.gem5_bin.resolve()
    args.gem5_root = args.gem5_root.resolve()
    args.runner = args.runner.resolve()
    args.firmware_dir = args.firmware_dir.resolve()
    args.source_root = args.source_root.resolve()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    args.output.mkdir(parents=True)

    tasks = []
    for family, profile in SLOPE_CASES:
        for change in CHANGES:
            for size in (8, 100):
                benchmark = f"bench_cf_{family}_{size}"
                name = f"{family}_{size}__{change}"
                tasks.append(("slopes", name, profile, change, benchmark, False))
    for family, profile in TRACE_CASES:
        for change in ("unchanged",) + CHANGES:
            benchmark = f"bench_iso_{family}_1"
            name = f"{family}__{change}"
            tasks.append(("traces", name, profile, change, benchmark, True))
    for size in NOP_SIZES:
        benchmark = f"bench_iso_nop16_{size}"
        tasks.append((
            "roi_size", benchmark, "baseline", "unchanged", benchmark, False
        ))

    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, task, args): task for task in tasks}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            task = futures[future]
            records.append(future.result())
            print(f"[{done:02d}/{len(tasks)}] {task[0]}/{task[1]}", flush=True)

    source_root = args.source_root.resolve()
    gem5_root = args.gem5_root.resolve()
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.uname()._asdict(),
        "scope": "new isolation tests; completed variants are consumed, not rerun",
        "aggregation": "steady median of ROI repetitions 1-9",
        "event_cost": "(steady cycles N=100 - steady cycles N=8) / 92",
        "source_repository": str(source_root),
        "source_commit": command_output(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"]
        ),
        "source_status": command_output(
            ["git", "-C", str(source_root), "status", "--short"]
        ),
        "gem5_repository": str(gem5_root),
        "gem5_commit": command_output(
            ["git", "-C", str(gem5_root), "rev-parse", "HEAD"]
        ),
        "gem5_status": command_output(
            ["git", "-C", str(gem5_root), "status", "--short"]
        ),
        "gem5_build_info": command_output([str(args.gem5_bin), "--build-info"]),
        "compiler": command_output(
            ["arm-none-eabi-gcc", "--version"]
        ).splitlines()[0],
        "runner": str(args.runner.resolve()),
        "runner_sha256": sha256(args.runner),
        "modeled_clock_hz": 170_000_000,
        "hardware_reference_clock_hz": 150_000_000,
        "runs": sorted(records, key=lambda row: (row["group"], row["name"])),
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

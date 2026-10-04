#!/usr/bin/env python3
"""Run the already-built isolation ELFs on a physical STM32G474RE.

The collector deliberately writes into a new directory, retains the complete
OpenOCD transcript for every ELF, and reports the same steady median and
N=100/N=8 slope used by the gem5 study.  It does not build or silently select
a different flash/cache configuration.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REP_RE = re.compile(r"MICROBENCH\s+name=(\S+)\s+rep=(\d+)\s+inner=(\d+)")
DEFAULT_BENCHMARKS = tuple(
    f"bench_cf_{family}_{size}"
    for family in (
        "baseline_nop16", "conditional_taken", "conditional_not_taken",
        "call_direct", "call_indirect", "jump_indirect",
    )
    for size in (8, 100)
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elf-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--microbenchmark-root", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=15.0)
    parser.add_argument("--placement", choices=("flash-art", "sram"), required=True)
    parser.add_argument("benchmarks", nargs="*", default=DEFAULT_BENCHMARKS)
    args = parser.parse_args()
    args.elf_dir = args.elf_dir.resolve()
    args.output = args.output.resolve()
    args.microbenchmark_root = args.microbenchmark_root.resolve()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    args.output.mkdir(parents=True)

    probe = args.microbenchmark_root / "openocd" / "stlink.cfg"
    target = args.microbenchmark_root / "board" / "stm32g474re" / "openocd.cfg"
    raw_rows: list[dict] = []
    records: list[dict] = []
    command_lines: list[str] = []
    for benchmark in args.benchmarks:
        elf = args.elf_dir / f"{benchmark}.elf"
        if not elf.is_file():
            raise SystemExit(f"missing ELF: {elf}")
        log = args.output / f"{benchmark}.openocd.log"
        command = [
            "openocd", "-f", str(probe), "-f", str(target),
            "-c", "init", "-c", "reset", "-c", "halt",
            "-c", "arm semihosting enable",
            "-c", f"program {elf} verify", "-c", "reset run",
        ]
        command_lines.append(" ".join(command))
        try:
            with log.open("w") as stream:
                proc = subprocess.run(
                    command, cwd=args.microbenchmark_root, stdout=stream,
                    stderr=subprocess.STDOUT, timeout=args.timeout_s, check=False,
                )
            status = proc.returncode
        except subprocess.TimeoutExpired:
            # OpenOCD remains attached after the firmware has printed; timeout
            # is the expected termination path.
            status = 124
        if status not in (0, 124, 143):
            raise RuntimeError(f"OpenOCD failed for {benchmark}: status {status}")

        matches = [
            (int(match.group(2)), int(match.group(3)))
            for match in REP_RE.finditer(log.read_text(errors="replace"))
            if match.group(1) == benchmark
        ]
        matches.sort()
        if [rep for rep, _ in matches] != list(range(10)):
            raise RuntimeError(f"{benchmark}: expected reps 0..9, got {matches}")
        for rep, cycles in matches:
            raw_rows.append({
                "benchmark": benchmark, "placement": args.placement,
                "rep": rep, "roi_cycles": cycles,
            })
        steady = statistics.median(cycles for rep, cycles in matches if rep > 0)
        records.append({
            "benchmark": benchmark, "placement": args.placement,
            "elf": str(elf), "elf_sha256": sha256(elf),
            "rep0_cycles": matches[0][1], "steady_median_cycles": steady,
            "steady_min_cycles": min(cycles for rep, cycles in matches if rep > 0),
            "steady_max_cycles": max(cycles for rep, cycles in matches if rep > 0),
            "openocd_log": str(log),
        })

    write_csv(args.output / "raw_repetitions.csv", raw_rows, list(raw_rows[0]))
    write_csv(args.output / "steady_summary.csv", records, list(records[0]))
    by_name = {row["benchmark"]: row for row in records}
    slopes = []
    for family in (
        "baseline_nop16", "conditional_taken", "conditional_not_taken",
        "call_direct", "call_indirect", "jump_indirect",
    ):
        n8 = by_name.get(f"bench_cf_{family}_8")
        n100 = by_name.get(f"bench_cf_{family}_100")
        if n8 and n100:
            slopes.append({
                "family": family, "placement": args.placement,
                "cycles_N8": n8["steady_median_cycles"],
                "cycles_N100": n100["steady_median_cycles"],
                "event_cost_cycles": (
                    n100["steady_median_cycles"] - n8["steady_median_cycles"]
                ) / 92,
            })
    if slopes:
        write_csv(args.output / "event_costs.csv", slopes, list(slopes[0]))
    (args.output / "COMMANDS.txt").write_text("\n".join(command_lines) + "\n")
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "placement_label": args.placement,
        "warning": "placement is a label; verify ELF map and symbol addresses",
        "aggregation": "median of repetitions 1-9; repetition 0 excluded",
        "event_cost": "(steady N=100 - steady N=8) / 92",
        "records": records,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

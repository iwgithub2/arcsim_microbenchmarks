#!/usr/bin/env python3
"""Build, archive, flash, and capture RP2350 isolation benchmarks.

Run this on the Mac that has the Pico SDK, toolchain, picotool, and board.  One
invocation represents one controlled configuration (for example folding on in
SRAM8); it refuses to overwrite its output directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


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


def command_output(command: list[str], cwd: Path) -> str:
    return subprocess.run(
        command, cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout


def symbols(nm: str, elf: Path) -> dict[str, str]:
    output = command_output([nm, "-n", str(elf)], elf.parent)
    wanted = {
        "bench_entry", "kernel_body", "load_word_same", "load_word_other",
        "__scratch_x_start__", "__scratch_x_end__",
        "__scratch_y_start__", "__scratch_y_end__",
    }
    found = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] in wanted:
            found[fields[-1]] = f"0x{int(fields[0], 16):08x}"
    return found


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rp2350-source", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", default="mac-pico2-sweep")
    parser.add_argument("--port")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--scratch-x", action="store_true")
    parser.add_argument("--control-flash", action="store_true")
    parser.add_argument("--disable-fold", action="store_true")
    parser.add_argument("--nm", default="arm-none-eabi-nm")
    parser.add_argument("--objdump", default="arm-none-eabi-objdump")
    parser.add_argument("benchmarks", nargs="*", default=DEFAULT_BENCHMARKS)
    args = parser.parse_args()
    if args.scratch_x and args.control_flash:
        parser.error("--scratch-x and --control-flash are mutually exclusive")
    args.rp2350_source = args.rp2350_source.resolve()
    args.build_dir = args.build_dir.resolve()
    args.output = args.output.resolve()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    args.output.mkdir(parents=True)
    artifacts = args.output / "artifacts"
    artifacts.mkdir()

    sys.path.insert(0, str(args.rp2350_source))
    from run_benchmark import capture_available_port, flash_uf2

    onoff = lambda value: "ON" if value else "OFF"
    configure = [
        "cmake", "--preset", args.preset,
        f"-DRP2350_CODE_SCRATCH_X={onoff(args.scratch_x)}",
        f"-DRP2350_CONTROL_FLASH={onoff(args.control_flash)}",
        f"-DRP2350_DISABLE_FOLD={onoff(args.disable_fold)}",
    ]
    configure_log = command_output(configure, args.rp2350_source)
    (args.output / "configure.log").write_text("$ " + " ".join(configure) + "\n" + configure_log)

    raw_rows: list[dict] = []
    records: list[dict] = []
    commands: list[str] = []
    for benchmark in args.benchmarks:
        reconfigure = [
            "cmake", "-S", str(args.rp2350_source), "-B", str(args.build_dir),
            f"-DRP2350_BENCH={benchmark}",
            f"-DRP2350_CODE_SCRATCH_X={onoff(args.scratch_x)}",
            f"-DRP2350_CONTROL_FLASH={onoff(args.control_flash)}",
            f"-DRP2350_DISABLE_FOLD={onoff(args.disable_fold)}",
        ]
        build = ["cmake", "--build", str(args.build_dir), "--target", "rp2350_benchmark"]
        commands.extend((" ".join(reconfigure), " ".join(build)))
        build_log = command_output(reconfigure, args.rp2350_source)
        build_log += command_output(build, args.rp2350_source)
        (args.output / f"{benchmark}.build.log").write_text(build_log)

        source_artifacts = {
            "elf": args.build_dir / "rp2350_benchmark.elf",
            "map": args.build_dir / "rp2350_benchmark.elf.map",
            "uf2": args.build_dir / "rp2350_benchmark.uf2",
        }
        for suffix, source in source_artifacts.items():
            if not source.is_file():
                raise RuntimeError(f"missing build artifact: {source}")
            shutil.copy2(source, artifacts / f"{benchmark}.{suffix}")
        elf = artifacts / f"{benchmark}.elf"
        uf2 = artifacts / f"{benchmark}.uf2"
        disassembly = command_output([args.objdump, "-d", "-S", str(elf)], artifacts)
        (artifacts / f"{benchmark}.disassembly.txt").write_text(disassembly)

        deadline = time.monotonic() + args.timeout_s
        flash_log = flash_uf2(uf2, update=True)
        port, capture = capture_available_port(benchmark, deadline, args.port)
        (args.output / f"{benchmark}.serial.log").write_text(
            flash_log + f"\nCAPTURE_PORT {port}\n" + capture.raw_text
        )
        if capture.sys_hz != 150_000_000:
            raise RuntimeError(f"{benchmark}: expected 150 MHz, got {capture.sys_hz}")
        if capture.core != "cortex-m33" or capture.core_id != 0 or capture.active_cores != 1:
            raise RuntimeError(f"{benchmark}: unexpected core header: {capture}")
        actlr = re.search(r"MICROBENCH_ACTLR value=0x([0-9a-fA-F]+) disfold=([01])", capture.raw_text)
        if actlr is None or int(actlr.group(2)) != int(args.disable_fold):
            raise RuntimeError(f"{benchmark}: ACTLR.DISFOLD readback does not match configuration")
        if [rep for rep, _ in capture.reps] != list(range(10)):
            raise RuntimeError(f"{benchmark}: expected repetitions 0..9")
        for rep, cycles in capture.reps:
            raw_rows.append({
                "benchmark": benchmark, "scratch_x": int(args.scratch_x),
                "control_flash": int(args.control_flash),
                "disable_fold": int(args.disable_fold), "rep": rep,
                "roi_cycles": cycles,
            })
        steady_values = [cycles for rep, cycles in capture.reps if rep > 0]
        final_symbols = symbols(args.nm, elf)
        if args.scratch_x:
            for name in ("bench_entry", "kernel_body"):
                if name not in final_symbols:
                    raise RuntimeError(f"{benchmark}: final ELF lacks {name}")
                address = int(final_symbols[name], 16) & ~1
                if not 0x20080000 <= address < 0x20081000:
                    raise RuntimeError(
                        f"{benchmark}: {name}={address:#010x} is outside SRAM8"
                    )
        if args.control_flash:
            for name in ("bench_entry", "kernel_body"):
                if name not in final_symbols:
                    raise RuntimeError(f"{benchmark}: final ELF lacks {name}")
                address = int(final_symbols[name], 16) & ~1
                if not 0x10000000 <= address < 0x14000000:
                    raise RuntimeError(
                        f"{benchmark}: {name}={address:#010x} is outside XIP flash"
                    )
        records.append({
            "benchmark": benchmark, "scratch_x": int(args.scratch_x),
            "control_flash": int(args.control_flash),
            "disable_fold": int(args.disable_fold), "sys_hz": capture.sys_hz,
            "actlr_value": "0x" + actlr.group(1),
            "elf_sha256": sha256(elf), "map_sha256": sha256(artifacts / f"{benchmark}.map"),
            "symbols": json.dumps(final_symbols, sort_keys=True),
            "rep0_cycles": capture.reps[0][1],
            "steady_median_cycles": statistics.median(steady_values),
            "steady_min_cycles": min(steady_values),
            "steady_max_cycles": max(steady_values),
        })

    write_csv(args.output / "raw_repetitions.csv", raw_rows, list(raw_rows[0]))
    write_csv(args.output / "steady_summary.csv", records, list(records[0]))
    (args.output / "COMMANDS.txt").write_text("\n".join(commands) + "\n")
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scratch_x": args.scratch_x, "control_flash": args.control_flash,
        "disable_fold": args.disable_fold,
        "aggregation": "median of repetitions 1-9; repetition 0 excluded",
        "records": records,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

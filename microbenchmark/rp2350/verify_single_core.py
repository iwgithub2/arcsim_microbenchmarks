#!/usr/bin/env python3
"""Fail a firmware build if it contains an RP2350 core-1 launch path."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nm", required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-sram8", action="store_true")
    parser.add_argument("--require-xip", action="store_true")
    args = parser.parse_args()

    symbols = subprocess.run(
        [args.nm, "-a", str(args.elf)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout
    forbidden = (
        "multicore_launch_core1",
        "multicore_launch_core1_raw",
        "multicore_launch_core1_with_stack",
        "core1_wrapper",
        "core1_trampoline",
    )
    found = [name for name in forbidden if name in symbols]
    if found:
        raise SystemExit(
            "single-core verification failed; linked core-1 symbols: "
            + ", ".join(found)
        )
    if " bench_entry" not in symbols or " main" not in symbols:
        raise SystemExit("single-core verification could not find benchmark entry points")

    memory_evidence = ""
    if args.require_sram8 or args.require_xip:
        addresses = {
            match.group(2): int(match.group(1), 16)
            for match in re.finditer(
                r"^([0-9a-fA-F]+)\s+\S\s+(\S+)$", symbols, re.MULTILINE
            )
        }
    if args.require_sram8:
        required = (
            "bench_entry", "kernel_body", "__scratch_x_start__",
            "__scratch_x_end__", "__StackTop", "__StackBottom",
            "__StackOneTop", "__StackOneBottom",
        )
        missing = [name for name in required if name not in addresses]
        if missing:
            raise SystemExit("SRAM placement symbols missing: " + ", ".join(missing))
        start = addresses["__scratch_x_start__"]
        end = addresses["__scratch_x_end__"]
        if not (0x20080000 <= start < end <= 0x20081000):
            raise SystemExit(f"Scratch X section is outside SRAM8: {start:#x}..{end:#x}")
        for name in ("bench_entry", "kernel_body"):
            if not start <= addresses[name] < end:
                raise SystemExit(f"{name} is outside SRAM8: {addresses[name]:#x}")
        if not (0x20081000 <= addresses["__StackBottom"] < addresses["__StackTop"] <= 0x20082000):
            raise SystemExit("core-0 stack is outside SRAM9")
        if addresses["__StackOneBottom"] != addresses["__StackOneTop"]:
            raise SystemExit("core-1 stack is nonempty")
        memory_evidence = (
            f"scratch_x_start={start:#010x}\n"
            f"scratch_x_end={end:#010x}\n"
            f"bench_entry={addresses['bench_entry']:#010x}\n"
            f"kernel_body={addresses['kernel_body']:#010x}\n"
            f"core0_stack_bottom={addresses['__StackBottom']:#010x}\n"
            f"core0_stack_top={addresses['__StackTop']:#010x}\n"
            "core1_stack_bytes=0\n"
        )
    if args.require_xip:
        required = ("bench_entry", "kernel_body")
        missing = [name for name in required if name not in addresses]
        if missing:
            raise SystemExit("XIP placement symbols missing: " + ", ".join(missing))
        for name in required:
            if not 0x10000000 <= addresses[name] < 0x14000000:
                raise SystemExit(f"{name} is outside XIP: {addresses[name]:#x}")
        memory_evidence = (
            f"bench_entry={addresses['bench_entry']:#010x}\n"
            f"kernel_body={addresses['kernel_body']:#010x}\n"
        )

    args.output.write_text(
        "single_core_verified=true\n"
        "active_core=0\n"
        "core1_stack_size=0\n"
        "core1_launch_symbols=absent\n"
        + memory_evidence
        + f"elf={args.elf.resolve()}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

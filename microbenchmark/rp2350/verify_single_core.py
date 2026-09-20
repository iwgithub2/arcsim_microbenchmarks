#!/usr/bin/env python3
"""Fail a firmware build if it contains an RP2350 core-1 launch path."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nm", required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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

    args.output.write_text(
        "single_core_verified=true\n"
        "active_core=0\n"
        "core1_stack_size=0\n"
        "core1_launch_symbols=absent\n"
        f"elf={args.elf.resolve()}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

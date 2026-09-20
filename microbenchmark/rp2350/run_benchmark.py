#!/usr/bin/env python3
"""Flash one RP2350 UF2 with picotool and capture its USB CDC results."""

from __future__ import annotations

import argparse
import glob
import os
import re
import select
import subprocess
import sys
import termios
import time
from dataclasses import dataclass
from pathlib import Path


REP_RE = re.compile(r"MICROBENCH\s+name=(\S+)\s+rep=(\d+)\s+inner=(\d+)")
HEADER_RE = re.compile(
    r"MICROBENCH_BOARD\s+board=(\S+)\s+core=(\S+)\s+"
    r"core_id=(\d+)\s+active_cores=(\d+)\s+implementation=(\S+)\s+"
    r"sys_hz=(\d+)\s+reps=(\d+)"
)


@dataclass(frozen=True)
class CaptureResult:
    bench: str
    board: str
    core: str
    core_id: int
    active_cores: int
    implementation: str
    sys_hz: int
    expected_reps: int
    reps: list[tuple[int, int]]
    raw_text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench", nargs="?", default="bench_nop16_100")
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--port", help="USB CDC path (auto-detected by default)")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    return parser.parse_args()


def find_serial_port(deadline: float) -> str:
    while time.monotonic() < deadline:
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        if ports:
            return ports[0]
        time.sleep(0.1)
    raise RuntimeError("timed out waiting for /dev/cu.usbmodem*")


def configure_tty(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def capture(port: str, bench: str, deadline: float) -> CaptureResult:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_tty(fd)
        data = bytearray()
        reps: dict[int, int] = {}
        header_values: tuple[str, str, int, int, str, int, int] | None = None
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.25)
            if readable:
                chunk = os.read(fd, 4096)
                if chunk:
                    data.extend(chunk)
                    text = data.decode("utf-8", errors="replace")
                    header = HEADER_RE.search(text)
                    if header:
                        header_values = (
                            header.group(1),
                            header.group(2),
                            int(header.group(3)),
                            int(header.group(4)),
                            header.group(5),
                            int(header.group(6)),
                            int(header.group(7)),
                        )
                    for match in REP_RE.finditer(text):
                        if match.group(1) == bench:
                            reps[int(match.group(2))] = int(match.group(3))
                    if header_values is not None and len(reps) >= header_values[6]:
                        (board, core, core_id, active_cores, implementation,
                         sys_hz, expected_reps) = header_values
                        return CaptureResult(
                            bench=bench,
                            board=board,
                            core=core,
                            core_id=core_id,
                            active_cores=active_cores,
                            implementation=implementation,
                            sys_hz=sys_hz,
                            expected_reps=expected_reps,
                            reps=sorted(reps.items()),
                            raw_text=text,
                        )
            time.sleep(0.01)
        if data:
            print(data.decode("utf-8", errors="replace"), file=sys.stderr)
        raise RuntimeError(f"timed out waiting for results from {bench}")
    finally:
        os.close(fd)


def capture_available_port(
    bench: str,
    deadline: float,
    requested_port: str | None = None,
) -> tuple[str, CaptureResult]:
    """Wait for the post-reboot CDC port and capture one complete result."""
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            port = requested_port or find_serial_port(deadline)
            return port, capture(port, bench, deadline)
        except OSError as exc:
            # The old /dev entry can briefly remain while the board reboots.
            last_error = exc
            time.sleep(0.1)
    if last_error is not None:
        raise RuntimeError(f"USB serial port never became usable: {last_error}")
    raise RuntimeError("timed out waiting for USB serial results")


def flash_uf2(uf2: Path, *, update: bool = False) -> str:
    cmd = ["picotool", "load", "-v", "-x", "-f"]
    if update:
        cmd.append("-u")
    cmd.append(str(uf2))
    proc = subprocess.run(
        cmd, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "picotool could not access or flash the RP2350 "
            f"(exit {proc.returncode}):\n{proc.stdout}"
        )
    return proc.stdout


def main() -> int:
    args = parse_args()
    uf2 = args.build_dir.resolve() / f"{args.bench}.uf2"
    if not uf2.exists():
        raise SystemExit(f"UF2 not found: {uf2}")

    deadline = time.monotonic() + args.timeout_s
    try:
        flash_output = flash_uf2(uf2)
    except RuntimeError as exc:
        raise SystemExit(
            f"{exc}\nFor the first flash, hold BOOTSEL while plugging in "
            "the board, release it after the RP2350 volume appears, and retry."
        ) from exc
    print(flash_output, end="" if flash_output.endswith("\n") else "\n")
    port, result = capture_available_port(args.bench, deadline, args.port)
    print(f"Capturing {port} ...")
    print(result.raw_text, end="" if result.raw_text.endswith("\n") else "\n")
    values = [cycles for _, cycles in result.reps]
    print(
        f"{args.bench}: reps={len(values)} min={min(values)} "
        f"avg={sum(values) / len(values):.1f} max={max(values)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

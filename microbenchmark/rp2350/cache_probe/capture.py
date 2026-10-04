#!/usr/bin/env python3
"""Capture one 20-repetition RP2350 cache probe over USB CDC."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_benchmark import configure_tty, find_serial_port

HEADER = re.compile(r"CACHE_PROBE_HEADER ([^\r\n]+)")
RECORD = re.compile(r"CACHE_PROBE rep=(\d+) ([^\r\n]+)")
FIELDS = ("cold_add32", "warm_add32", "conflict2", "conflict3",
          "mixed_same", "mixed_opposite", "chain_same", "chain_opposite")


def pairs(line):
    return dict(re.findall(r"([a-z0-9_]+)=(0x[0-9a-fA-F]+|\d+)", line))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--timeout-s", type=float, default=60)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error(f"refusing to overwrite {output}")
    build = args.build_dir.resolve()
    elf = build / "rp2350_cache_probe.elf"
    uf2 = build / "rp2350_cache_probe.uf2"
    if not elf.is_file() or not uf2.is_file():
        parser.error("build directory needs rp2350_cache_probe.elf and .uf2")
    deadline = time.monotonic() + args.timeout_s
    port = args.port or find_serial_port(deadline)
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    data = bytearray()
    try:
        configure_tty(fd)
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.25)
            if readable:
                chunk = os.read(fd, 4096)
                if chunk:
                    data.extend(chunk)
                    raw = data.decode("utf-8", errors="replace")
                    if HEADER.search(raw) and len(RECORD.findall(raw)) >= 20:
                        break
        else:
            raise RuntimeError(f"timed out after {args.timeout_s}s; received {len(data)} bytes")
    finally:
        os.close(fd)
    raw = data.decode("utf-8", errors="replace")
    header = pairs(HEADER.search(raw).group(1))
    rows = []
    for match in RECORD.finditer(raw):
        row = {"rep": int(match.group(1))}
        row.update({key: int(value) for key, value in pairs(match.group(2)).items()})
        rows.append(row)
    if [row["rep"] for row in rows] != list(range(20)):
        raise RuntimeError(f"expected reps 0..19, got {[row['rep'] for row in rows]}")
    if any(set(row) != {"rep", *FIELDS} for row in rows):
        raise RuntimeError("one or more cycle fields are missing")
    if int(header["sys_hz"]) != 150_000_000 or int(header["disfold"]) != 1:
        raise RuntimeError(f"unexpected clock or folding: {header}")
    addresses = {key: int(header[key], 16) for key in
                 ("code", "chain", "a", "b", "c", "same", "opposite")}
    if (addresses["b"] - addresses["a"] != 0x2000 or
            addresses["c"] - addresses["b"] != 0x2000 or
            addresses["chain"] & 15 or
            (addresses["same"] ^ addresses["opposite"]) & 8 != 8):
        raise RuntimeError(f"unexpected XIP layout: {header}")
    output.mkdir(parents=True)
    (output / "serial.log").write_text(raw)
    with (output / "cycles.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("rep", *FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    for source in (elf, uf2):
        shutil.copy2(source, output / source.name)
    manifest = {"port": port, "header": header,
                "elf_sha256": sha256(elf), "uf2_sha256": sha256(uf2)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Captured 20 reps from {port} in {output}")


if __name__ == "__main__":
    main()

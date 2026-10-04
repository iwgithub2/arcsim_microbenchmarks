#!/usr/bin/env python3
"""Reduce new timing isolation runs and compare them with completed results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
import statistics
import subprocess
from pathlib import Path


STAT_FIELDS = {
    "cycles": "system.cpu.numCycles",
    "committed_insts": "system.cpu.commitStats0.numInsts",
    "fetched_insts": "system.cpu.fetchStats0.numInsts",
    "discarded_ops": "system.cpu.executeStats0.numDiscardedOps",
    "fetch_suspends": "system.cpu.fetchStats0.numFetchSuspends",
    "direct_cond_committed": "system.cpu.branchPred.committed_0::DirectCond",
    "direct_cond_mispred": "system.cpu.branchPred.mispredicted_0::DirectCond",
    "call_indirect_committed": "system.cpu.branchPred.committed_0::CallIndirect",
    "call_indirect_mispred": "system.cpu.branchPred.mispredicted_0::CallIndirect",
    "indirect_uncond_committed": "system.cpu.branchPred.committed_0::IndirectUncond",
    "indirect_uncond_mispred": "system.cpu.branchPred.mispredicted_0::IndirectUncond",
    "return_committed": "system.cpu.branchPred.committed_0::Return",
    "return_mispred": "system.cpu.branchPred.mispredicted_0::Return",
    "corrected_total": "system.cpu.branchPred.corrected_0::total",
    "early_resteers_total": "system.cpu.branchPred.earlyResteers_0::total",
    "btb_lookups": "system.cpu.branchPred.BTBLookups",
    "btb_hits": "system.cpu.branchPred.BTBHits",
    "btb_mispredicted": "system.cpu.branchPred.BTBMispredicted",
    "indirect_lookups": "system.cpu.branchPred.indirectLookups",
    "indirect_hits": "system.cpu.branchPred.indirectHits",
    "indirect_misses": "system.cpu.branchPred.indirectMisses",
    "icache_hits": "system.art_icache.demandHits::cpu.inst",
    "icache_misses": "system.art_icache.demandMisses::cpu.inst",
    "icache_wait_events": "system.art_icache.cpuWaitEvents",
}
HARDWARE = {"conditional_taken": 2.5, "call_indirect": 4.0, "jump_indirect": 5.0}
REFERENCE = {
    "conditional_taken": ("local_bp_btb256", 3.565217),
    "call_indirect": ("baseline_a", 12.0),
    "jump_indirect": ("icache2k", 9.0),
}


def stat_windows(text: str) -> list[dict[str, float]]:
    rows = []
    for body in text.split("---------- Begin Simulation Statistics ----------")[1:]:
        body = body.split("---------- End Simulation Statistics ----------")[0]
        values = {}
        for line in body.splitlines():
            match = re.match(r"^(\S+)\s+([-+\w.]+)", line)
            if match:
                try:
                    values[match.group(1)] = float(match.group(2))
                except ValueError:
                    pass
        rows.append(values)
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_gap_svg(path: Path, rows: list[dict]) -> None:
    """Write a dependency-free cycle plot; elapsed time stays in the CSV."""
    rows = sorted(rows, key=lambda row: row["family"])
    width, left, right, top, row_h = 940, 190, 30, 70, 31
    plot_width = width - left - right
    height = top + row_h * len(rows) + 55
    max_cycles = 12.0
    scale = plot_width / max_cycles
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:system-ui,sans-serif;fill:#222}.label{font-size:12px}.tick{font-size:11px;fill:#555}.title{font-size:17px;font-weight:600}.legend{font-size:12px}</style>',
        '<text class="title" x="190" y="25">Control-flow event cost: supplied M33 vs M4 baseline and best controlled result</text>',
    ]
    for tick in range(0, 13, 2):
        x = left + tick * scale
        parts.append(f'<line x1="{x:.1f}" y1="45" x2="{x:.1f}" y2="{height-35}" stroke="#ddd"/>')
        parts.append(f'<text class="tick" x="{x:.1f}" y="{height-17}" text-anchor="middle">{tick}</text>')
    colors = (("hardware_event_cost_cycles", "#555"),
              ("original_gem5_event_cost_cycles", "#d55e00"),
              ("best_controlled_event_cost_cycles", "#0072b2"))
    for index, row in enumerate(rows):
        y = top + index * row_h
        parts.append(f'<text class="label" x="{left-8}" y="{y+12}" text-anchor="end">{html.escape(row["family"])}</text>')
        for offset, (field, color) in enumerate(colors):
            value = float(row[field])
            parts.append(f'<rect x="{left}" y="{y+offset*6}" width="{value*scale:.2f}" height="5" fill="{color}"/>')
    legend = (("RP2350 M33", "#555"), ("original gem5 M4", "#d55e00"),
              ("best controlled M4", "#0072b2"))
    x = left
    for label, color in legend:
        parts.append(f'<rect x="{x}" y="38" width="14" height="6" fill="{color}"/>')
        parts.append(f'<text class="legend" x="{x+19}" y="45">{label}</text>')
        x += 170
    parts.append(f'<text class="tick" x="{left+plot_width/2:.1f}" y="{height-2}" text-anchor="middle">cycles per complete repeated pattern</text>')
    parts.append('</svg>')
    path.write_text("\n".join(parts) + "\n")


def steady_median(rows: list[dict], field: str) -> float:
    return statistics.median(row[field] for row in rows[1:10])


def dump_section(tool: str, source: Path, output: Path) -> bytes:
    subprocess.run(
        [tool, "--dump-section", f".text.kernel_body={output}", str(source)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return output.read_bytes()


def symbol_info(tool: str, source: Path) -> tuple[int, int]:
    text = subprocess.run(
        [tool, "-S", str(source)], text=True, check=True,
        stdout=subprocess.PIPE,
    ).stdout
    match = re.search(
        r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+[tT]\s+kernel_body$",
        text, re.MULTILINE,
    )
    if not match:
        raise RuntimeError(f"kernel_body missing: {source}")
    return int(match.group(1), 16), int(match.group(2), 16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-runs", type=Path, required=True)
    parser.add_argument("--completed", type=Path, required=True)
    parser.add_argument("--full-baseline", type=Path, required=True)
    parser.add_argument("--hardware-reference", type=Path, required=True)
    parser.add_argument("--binaries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--objcopy", default="arm-none-eabi-objcopy")
    parser.add_argument("--nm", default="arm-none-eabi-nm")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    hardware = {}
    with args.hardware_reference.open(newline="") as stream:
        for row in csv.DictReader(stream):
            hardware[row["family"]] = float(row["event_cost_cycles"])
    baseline_by_size = {}
    with args.full_baseline.open(newline="") as stream:
        for row in csv.DictReader(stream):
            match = re.match(r"bench_cf_(.+)_(8|100)$", row["benchmark"])
            if match:
                baseline_by_size[(match.group(1), int(match.group(2)))] = float(
                    row["steady_median_cycles"]
                )
    completed_costs = {
        family: {
            "hardware": hardware[family],
            "baseline": (baseline_by_size[family, 100] - baseline_by_size[family, 8]) / 92,
        }
        for family in hardware
    }
    best_overrides = {
        "conditional_taken": ("local_bp_btb256", 3.565217,
                              "predictor/BTB recovery"),
        "jump_indirect": ("icache2k", 9.0, "I-cache capacity artifact"),
    }
    remaining = []
    for family, values in completed_costs.items():
        best_config, best_cost, cause = best_overrides.get(
            family, ("baseline_a", values["baseline"], "none isolated")
        )
        original_excess = values["baseline"] - values["hardware"]
        explained = values["baseline"] - best_cost
        remaining.append({
            "family": family,
            "hardware_event_cost_cycles": f"{values['hardware']:.6f}",
            "original_gem5_event_cost_cycles": f"{values['baseline']:.6f}",
            "best_controlled_configuration": best_config,
            "best_controlled_event_cost_cycles": f"{best_cost:.6f}",
            "original_excess_cycles": f"{original_excess:.6f}",
            "directly_explained_cycles": f"{explained:.6f}",
            "remaining_excess_cycles": f"{best_cost-values['hardware']:.6f}",
            "cycle_ratio": f"{best_cost/values['hardware']:.6f}",
            "hardware_event_cost_ns_at_150MHz": f"{values['hardware']/150_000_000*1e9:.6f}",
            "gem5_event_cost_ns_at_170MHz": f"{best_cost/170_000_000*1e9:.6f}",
            "elapsed_time_ratio": f"{(best_cost/170_000_000)/(values['hardware']/150_000_000):.6f}",
            "isolated_cause": cause,
        })
    write_csv(
        args.output / "remaining_gap_by_family.csv", sorted(remaining, key=lambda r: r["family"]),
        list(remaining[0]),
    )
    write_gap_svg(args.output / "remaining_gap_cycles.svg", remaining)

    raw = []
    for group in ("slopes", "traces", "roi_size"):
        for run_dir in sorted((args.new_runs / "runs" / group).iterdir()):
            simout = (run_dir / "simout.txt").read_text()
            profile = re.search(r"^PROFILE (.+)$", simout, re.MULTILINE).group(1)
            change = re.search(r"^CHANGE (.+)$", simout, re.MULTILINE).group(1)
            firmware = re.search(r"^FIRMWARE (.+)$", simout, re.MULTILINE).group(1)
            benchmark = Path(firmware).stem
            windows = stat_windows((run_dir / "stats.txt").read_text())[:10]
            for rep, window in enumerate(windows):
                row = {
                    "group": group, "run": run_dir.name, "profile": profile,
                    "change": change, "benchmark": benchmark, "rep": rep,
                }
                for name, stat in STAT_FIELDS.items():
                    row[name] = window.get(stat, 0.0)
                raw.append(row)
    raw_fields = ["group", "run", "profile", "change", "benchmark", "rep"] + list(STAT_FIELDS)
    write_csv(args.output / "raw_roi_counters.csv", raw, raw_fields)

    grouped = {}
    for row in raw:
        grouped.setdefault((row["group"], row["run"]), []).append(row)

    slopes = []
    for family in HARDWARE:
        for change in ("fetch_limit1", "fetch2_decode2", "decode_execute2", "two_fetch_stage"):
            medians = {}
            counters = {}
            for size in (8, 100):
                rows = grouped[("slopes", f"{family}_{size}__{change}")]
                medians[size] = steady_median(rows, "cycles")
                counters[size] = {
                    field: steady_median(rows, field) for field in STAT_FIELDS
                }
            event = (medians[100] - medians[8]) / 92
            reference = REFERENCE[family][1]
            slopes.append({
                "family": family, "residual_reference": REFERENCE[family][0],
                "change": change, "cycles_N8": medians[8],
                "cycles_N100": medians[100], "event_cost_cycles": f"{event:.6f}",
                "hardware_event_cost_cycles": HARDWARE[family],
                "excess_over_hardware": f"{event-HARDWARE[family]:.6f}",
                "delta_from_residual_reference": f"{event-reference:.6f}",
                "cycle_ratio": f"{event/HARDWARE[family]:.6f}",
                "mispred_delta_N100_minus_N8": f"{sum(counters[100][f] - counters[8][f] for f in ('direct_cond_mispred','call_indirect_mispred','indirect_uncond_mispred','return_mispred')):.3f}",
                "fetched_delta_N100_minus_N8": f"{counters[100]['fetched_insts']-counters[8]['fetched_insts']:.3f}",
                "icache_miss_delta_N100_minus_N8": f"{counters[100]['icache_misses']-counters[8]['icache_misses']:.3f}",
                "wait_delta_N100_minus_N8": f"{counters[100]['icache_wait_events']-counters[8]['icache_wait_events']:.3f}",
            })
    write_csv(args.output / "event_cost_stage_sensitivity.csv", slopes, list(slopes[0]))

    roi = []
    for size in (0, 1, 2, 4, 8, 16, 32, 64, 100):
        rows = grouped[("roi_size", f"bench_iso_nop16_{size}")]
        roi.append({
            "nop_count": size, "rep0_cycles": rows[0]["cycles"],
            "steady_median_cycles": steady_median(rows, "cycles"),
            "steady_min_cycles": min(row["cycles"] for row in rows[1:10]),
            "steady_max_cycles": max(row["cycles"] for row in rows[1:10]),
        })
    xs = [row["nop_count"] for row in roi]
    ys = [row["steady_median_cycles"] for row in roi]
    xbar, ybar = statistics.fmean(xs), statistics.fmean(ys)
    slope = sum((x-xbar)*(y-ybar) for x, y in zip(xs, ys)) / sum((x-xbar)**2 for x in xs)
    intercept = ybar - slope*xbar
    for row in roi:
        row["fit_cycles"] = f"{intercept+slope*row['nop_count']:.6f}"
        row["residual_cycles"] = f"{row['steady_median_cycles']-(intercept+slope*row['nop_count']):.6f}"
    write_csv(args.output / "roi_size_sweep.csv", roi, list(roi[0]))
    (args.output / "roi_size_fit.txt").write_text(
        f"steady_cycles = {intercept:.9f} + {slope:.9f} * nop_count\n"
    )

    traces = []
    reasons = (
        "CorrectlyPredictedBranch", "UnpredictedBranch",
        "BadlyPredictedBranch", "BadlyPredictedBranchTarget",
    )
    for run_dir in sorted((args.new_runs / "runs" / "traces").iterdir()):
        text = (run_dir / "minortrace.log").read_text(errors="replace")
        rows = grouped[("traces", run_dir.name)]
        row = {
            "run": run_dir.name,
            "steady_median_cycles": steady_median(rows, "cycles"),
            "steady_median_fetched_insts": steady_median(rows, "fetched_insts"),
            "steady_median_corrected": steady_median(rows, "corrected_total"),
            "steady_median_icache_misses": steady_median(rows, "icache_misses"),
            "steady_median_wait_events": steady_median(rows, "icache_wait_events"),
            "minortrace_cycle_records": text.count("MinorTrace: state="),
            "fetch_discard_messages": len(re.findall(r"Discarding|discarded", text)),
        }
        for reason in reasons:
            row[reason] = text.count(f"Branch data signalled: {reason}")
        traces.append(row)
    write_csv(args.output / "trace_event_counts.csv", traces, list(traces[0]))

    inventory = []
    body_dir = args.output / "kernel_bytes"
    body_dir.mkdir(exist_ok=True)
    names = [
        f"bench_cf_{family}_{size}"
        for family in ("conditional_taken", "call_indirect", "jump_indirect")
        for size in (8, 100)
    ] + [
        "bench_iso_forward_taken_1", "bench_iso_call_indirect_1",
        "bench_iso_jump_indirect_1",
    ]
    for name in names:
        by_platform = {}
        for platform in ("gem5", "stm32-flash", "rp2350-objects"):
            suffix = ".o" if platform == "rp2350-objects" else ".elf"
            source = args.binaries / platform / f"{name}{suffix}"
            if not source.exists():
                continue
            body_file = body_dir / f"{name}.{platform}.bin"
            body = dump_section(args.objcopy, source, body_file)
            address, size = symbol_info(args.nm, source)
            digest = hashlib.sha256(body).hexdigest()
            by_platform[platform] = digest
            inventory.append({
                "benchmark": name, "platform_build": platform,
                "artifact": str(source.resolve()),
                "kernel_body_address": f"0x{address:08x}",
                "address_mod_4": address % 4, "body_size_bytes": size,
                "body_sha256": digest,
            })
        if "gem5" in by_platform:
            for row in inventory:
                if row["benchmark"] == name:
                    row["body_matches_gem5"] = by_platform["gem5"] == row["body_sha256"]
    write_csv(args.output / "binary_inventory.csv", inventory, list(inventory[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run a new one-factor MinorCPU timing isolation with ROI-scoped tracing."""

import argparse

import m5
from m5 import debug as m5_debug
from m5.objects import Root
from m5.objects.ArmSemihosting import ArmSemihosting
from m5.objects.BranchPredictor import LocalBP, SimpleBTB
from m5.stats import dump as stats_dump
from m5.stats import reset as stats_reset

from gem5.prebuilt.cortexm.boards import STM32G474RETimingBoard


parser = argparse.ArgumentParser()
parser.add_argument("--firmware", required=True)
parser.add_argument(
    "--profile", choices=("baseline", "forward_recovered", "jump_icache2k"),
    required=True,
)
parser.add_argument(
    "--change",
    choices=(
        "unchanged", "fetch_limit1", "fetch2_decode2",
        "decode_execute2", "two_fetch_stage",
    ),
    required=True,
)
parser.add_argument("--trace", action="store_true")
parser.add_argument("--tick-limit", type=int, default=10_000_000_000_000)
args = parser.parse_args()

board = STM32G474RETimingBoard(enable_art=True)
board.semihosting = ArmSemihosting(mem_reserve="0B", stack_size="0B")
board.set_workload(args.firmware)
board.exit_on_work_items = True

if args.profile == "forward_recovered":
    board.cpu.branchPred.btb = SimpleBTB(
        numEntries=256, tagBits=16, instShiftAmt=1
    )
    board.cpu.branchPred.conditionalBranchPred = LocalBP(
        localPredictorSize=256, localCtrBits=2
    )
elif args.profile == "jump_icache2k":
    board.art_icache.size = "2KiB"

if args.change == "fetch_limit1":
    board.cpu.fetch1FetchLimit = 1
elif args.change == "fetch2_decode2":
    board.cpu.fetch2ToDecodeForwardDelay = 2
elif args.change == "decode_execute2":
    board.cpu.decodeToExecuteForwardDelay = 2
elif args.change == "two_fetch_stage":
    board.cpu.singleFetchStage = False
    board.cpu.fetch1ToFetch2ForwardDelay = 1
    board.cpu.fetch1ToFetch2BackwardDelay = 1

root = Root(full_system=True, system=board)
m5.instantiate()
print(f"PROFILE {args.profile}")
print(f"CHANGE {args.change}")
print(f"TRACE {int(args.trace)}")
print(f"FIRMWARE {args.firmware}")

trace_flags = ("MinorTrace", "MinorCPU", "MinorExecute", "MinorMem",
               "MinorTiming", "Branch", "Fetch", "ARTCache")

while True:
    remaining = args.tick_limit - m5.curTick()
    if remaining <= 0:
        print(f"Tick limit reached at {m5.curTick()}")
        break
    event = m5.simulate(remaining)
    cause = event.getCause()
    if cause == "workbegin":
        print(f"*** ROI BEGIN at tick {m5.curTick()} ***")
        stats_reset()
        if args.trace:
            for flag in trace_flags:
                m5_debug.flags[flag].enable()
    elif cause == "workend":
        print(f"*** ROI END at tick {m5.curTick()} ***")
        if args.trace:
            for flag in trace_flags:
                m5_debug.flags[flag].disable()
        stats_dump()
    elif "Stopped" in cause or "exit" in cause.lower():
        print(f"Exiting @ tick {m5.curTick()} because {cause}")
        break
    elif cause == "simulate() limit reached":
        print(f"Tick limit reached at {m5.curTick()}")
        break
    else:
        print(f"Unhandled exit event: {cause} @ tick {m5.curTick()}")
        break

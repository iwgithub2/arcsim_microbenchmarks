#!/usr/bin/env python3
"""Materialize the Cortex-M33 control-flow kernels in this directory."""

from pathlib import Path


HERE = Path(__file__).resolve().parent
COUNTS = (8, 9, 16, 100)


def body(family: str, count: int) -> str:
    repeat = f"        .rept {count}\n"
    end = "        .endr\n"
    if family == "baseline_nop16":
        return repeat + "        nop.n\n" + end
    if family == "direct_forward16":
        return repeat + "        b.n 1f\n        nop.n\n1:      nop.n\n" + end
    if family == "direct_forward32":
        return repeat + "        b.w 1f\n        nop.n\n1:      nop.n\n" + end
    if family == "conditional_taken":
        return "        movs r1, #0\n" + repeat + "        beq.n 1f\n        nop.n\n1:      nop.n\n" + end
    if family == "conditional_not_taken":
        return "        movs r1, #1\n" + repeat + "        beq.n 1f\n        nop.n\n1:      nop.n\n" + end
    if family in ("loop_body1", "loop_body4"):
        nops = 1 if family == "loop_body1" else 4
        return (
            f"        movw r1, #{count}\n1:\n"
            + "        nop.n\n" * nops
            + "        subs r1, r1, #1\n        bne.n 1b\n"
        )
    if family == "call_direct":
        return "        push {r6, lr}\n" + repeat + "        bl 2f\n" + end + "        pop {r6, pc}\n2:      nop.n\n        bx lr\n"
    if family == "call_indirect":
        return "        push {r6, lr}\n        adr r2, 2f\n        adds r2, r2, #1\n" + repeat + "        blx r2\n" + end + "        pop {r6, pc}\n2:      nop.n\n        bx lr\n"
    if family == "jump_indirect":
        return repeat + "        adr r2, 1f\n        adds r2, r2, #1\n        bx r2\n        nop.n\n1:      nop.n\n" + end
    if family == "target_aligned":
        return repeat + "        b.n 1f\n        nop.n\n        .balign 4\n1:      nop.n\n" + end
    if family == "target_halfword":
        return repeat + "        b.n 1f\n        nop.n\n        .balign 4\n        nop.n\n1:      nop.n\n" + end
    if family == "cbz_taken":
        return "        movs r1, #0\n" + repeat + "        cbz r1, 1f\n        nop.n\n1:      nop.n\n" + end
    if family == "cmpbeq_taken":
        return "        movs r1, #0\n" + repeat + "        cmp r1, #0\n        beq.n 1f\n        nop.n\n1:      nop.n\n" + end
    raise ValueError(family)


FAMILIES = (
    "baseline_nop16",
    "direct_forward16",
    "direct_forward32",
    "conditional_taken",
    "conditional_not_taken",
    "loop_body1",
    "loop_body4",
    "call_direct",
    "call_indirect",
    "jump_indirect",
    "target_aligned",
    "target_halfword",
    "cbz_taken",
    "cmpbeq_taken",
)


def main() -> None:
    for family in FAMILIES:
        for count in COUNTS:
            name = f"bench_cf_{family}_{count}"
            source = (
                f"/* {name}: {count} control-flow events on RP2350 Cortex-M33.\n"
                " * Both benchmark code and its timing wrapper execute from non-striped\n"
                " * SRAM8/Scratch X. See README.md for interpretation and controls.\n"
                " */\n"
                '#include "harness.h"\n\n'
                "    BENCH\n"
                + body(family, count)
                + "    END_BENCH\n"
            )
            (HERE / f"{name}.S").write_text(source)
    print(f"Generated {len(FAMILIES) * len(COUNTS)} Cortex-M33 control-flow kernels")


if __name__ == "__main__":
    main()

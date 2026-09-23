/* bench_cf_conditional_not_taken_9: 9 control-flow events on RP2350 Cortex-M33.
 * Both benchmark code and its timing wrapper execute from non-striped
 * SRAM8/Scratch X. See README.md for interpretation and controls.
 */
#include "harness.h"

    BENCH
        movs r1, #1
        .rept 9
        beq.n 1f
        nop.n
1:      nop.n
        .endr
    END_BENCH

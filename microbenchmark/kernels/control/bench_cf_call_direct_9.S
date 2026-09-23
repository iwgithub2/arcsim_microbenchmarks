/* bench_cf_call_direct_9: 9 control-flow events on RP2350 Cortex-M33.
 * Both benchmark code and its timing wrapper execute from non-striped
 * SRAM8/Scratch X. See README.md for interpretation and controls.
 */
#include "harness.h"

    BENCH
        push {r6, lr}
        .rept 9
        bl 2f
        .endr
        pop {r6, pc}
2:      nop.n
        bx lr
    END_BENCH

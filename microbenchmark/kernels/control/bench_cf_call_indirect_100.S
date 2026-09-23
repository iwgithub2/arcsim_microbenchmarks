/* bench_cf_call_indirect_100: 100 control-flow events on RP2350 Cortex-M33.
 * Both benchmark code and its timing wrapper execute from non-striped
 * SRAM8/Scratch X. See README.md for interpretation and controls.
 */
#include "harness.h"

    BENCH
        push {r6, lr}
        adr r2, 2f
        adds r2, r2, #1
        .rept 100
        blx r2
        .endr
        pop {r6, pc}
2:      nop.n
        bx lr
    END_BENCH

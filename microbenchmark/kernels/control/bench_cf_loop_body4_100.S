/* bench_cf_loop_body4_100: 100 control-flow events on RP2350 Cortex-M33.
 * Both benchmark code and its timing wrapper execute from non-striped
 * SRAM8/Scratch X. See README.md for interpretation and controls.
 */
#include "harness.h"

    BENCH
        movw r1, #100
1:
        nop.n
        nop.n
        nop.n
        nop.n
        subs r1, r1, #1
        bne.n 1b
    END_BENCH

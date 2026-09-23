/* bench_cf_direct_forward16_100: 100 control-flow events on RP2350 Cortex-M33.
 * Both benchmark code and its timing wrapper execute from non-striped
 * SRAM8/Scratch X. See README.md for interpretation and controls.
 */
#include "harness.h"

    BENCH
        .rept 100
        b.n 1f
        nop.n
1:      nop.n
        .endr
    END_BENCH

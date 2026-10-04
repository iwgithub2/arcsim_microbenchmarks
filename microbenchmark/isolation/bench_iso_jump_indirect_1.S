#include "harness.h"

    BENCH
        adr  r2, 1f
        adds r2, r2, #1
        bx   r2
        nop.n
1:      nop.n
    END_BENCH

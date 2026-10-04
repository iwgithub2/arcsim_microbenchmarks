#include "harness.h"

    BENCH
        push {r6, lr}
        adr  r2, 1f
        adds r2, r2, #1
        blx  r2
        pop  {r6, pc}
1:      nop.n
        bx   lr
    END_BENCH

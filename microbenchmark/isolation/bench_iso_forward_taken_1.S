#include "harness.h"

    BENCH
        movs r1, #0
        beq.n 1f
        nop.n
1:      nop.n
    END_BENCH

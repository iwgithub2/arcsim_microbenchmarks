#include "harness.h"

    BENCH
        .rept ISO_N
        b.n 1f
        nop.n
        .balign 4
#if ISO_HALFWORD
        nop.n
#endif
1:
#if ISO_WIDTH32
        nop.w
#else
        nop.n
#endif
        .endr
    END_BENCH

#include "harness.h"

#ifndef ISO_N
#error "ISO_N must be defined"
#endif

    BENCH
        .rept ISO_N
        nop.n
        .endr
    END_BENCH

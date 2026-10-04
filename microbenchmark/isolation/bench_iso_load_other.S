/* N repeated loads; code is in SRAM8, data in SRAM9/Scratch Y. */
#include "harness.h"

#ifndef ISO_N
#error "ISO_N must be defined"
#endif

    BENCH
        movw r1, #:lower16:load_word_other
        movt r1, #:upper16:load_word_other
        .rept ISO_N
        ldr r2, [r1]
        .endr
    END_BENCH

    .section .scratch_y.load_data, "aw", %progbits
    .balign 4
load_word_other:
    .word 42

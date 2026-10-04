/* N repeated loads while code and data both reside in SRAM8/Scratch X. */
#include "harness.h"

#ifndef ISO_N
#error "ISO_N must be defined"
#endif

    BENCH
        movw r1, #:lower16:load_word_same
        movt r1, #:upper16:load_word_same
        .rept ISO_N
        ldr r2, [r1]
        .endr
    END_BENCH

    .section .scratch_x.load_data, "aw", %progbits
    .balign 4
load_word_same:
    .word 42

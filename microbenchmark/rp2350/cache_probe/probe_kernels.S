.syntax unified
.cpu cortex-m33
.thumb

/* XIP instruction stream: one 32-bit ADD per repetition. */
.section .text.cache_probe, "ax", %progbits
.balign 8
.global xip_add32
.thumb_func
xip_add32:
    movs r0, #0
    .rept 100
    adds.w r0, r0, #1
    .endr
    bx lr

/* One 8-byte XIP instruction line, repeatedly entered from SRAM. */
.balign 8
.global xip_load
.thumb_func
xip_load:
    ldr r0, [r0]
    bx lr

/* Each load/branch pair fetches from a bank-0 XIP line 16 bytes apart. */
.balign 16
.global xip_chain_load
.thumb_func
xip_chain_load:
    .rept 100
    ldr r3, [r0]
    b 1f
    .balign 16
1:
    .endr
    bx lr

/* SRAM code reads either two or three XIP lines mapping to one set. */
.section .scratch_x.cache_probe, "ax", %progbits
.balign 8
.global conflict2
.thumb_func
conflict2:
    .rept 100
    ldr r3, [r0]
    ldr r3, [r1]
    .endr
    bx lr

.balign 8
.global conflict3
.thumb_func
conflict3:
    .rept 100
    ldr r3, [r0]
    ldr r3, [r1]
    ldr r3, [r2]
    .endr
    bx lr

/* r0 = XIP data address; r1 = tiny XIP function. */
.balign 8
.global mixed_sram
.thumb_func
mixed_sram:
    push {r4, r5, lr}
    mov r4, r0
    mov r5, r1
    .rept 100
    mov r0, r4
    blx r5
    .endr
    pop {r4, r5, pc}

/* Each 8 KiB stride retains the same cache bank and set index. */
.section .rodata.cache_probe_data, "a", %progbits
.balign 8192
.global xip_data_a
xip_data_a: .word 17
.balign 8192
.global xip_data_b
xip_data_b: .word 23
.balign 8192
.global xip_data_c
xip_data_c: .word 29

.balign 16
.global xip_bank_data
xip_bank_data:
    .word 31, 0, 37, 0

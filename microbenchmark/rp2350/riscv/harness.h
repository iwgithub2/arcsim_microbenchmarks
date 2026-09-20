/* Single-core Hazard3 measurement harness for generated RP2350 kernels. */
#ifndef MICROBENCH_RP2350_RISCV_HARNESS_H
#define MICROBENCH_RP2350_RISCV_HARNESS_H

#ifndef __ASSEMBLER__
#error "This header is for RISC-V assembly benchmark sources"
#endif

    .option push
    .option rvc

    .macro RISCV_BENCH
        .section .text.kernel_body,"ax",@progbits
        .p2align 4
        .global kernel_body
        .type kernel_body, @function
kernel_body:
    .endm

    .macro RISCV_END_BENCH
        ret
        .size kernel_body, . - kernel_body

        .section .text.bench_entry,"ax",@progbits
        .p2align 2
        .global bench_entry
        .type bench_entry, @function
bench_entry:
        addi sp, sp, -16
        sw ra, 12(sp)
        sw s0, 8(sp)
        sw s1, 4(sp)
        sw s2, 0(sp)

        /* Untimed warmup call, followed by INNER_REPS measured calls. */
        call kernel_body
        la s0, _inner_delta_cyc
        li s1, INNER_REPS
1:
        csrr s2, mcycle
        call kernel_body
        csrr t0, mcycle
        sub t0, t0, s2
        sw t0, 0(s0)
        addi s0, s0, 4
        addi s1, s1, -1
        bnez s1, 1b

        lw s2, 0(sp)
        lw s1, 4(sp)
        lw s0, 8(sp)
        lw ra, 12(sp)
        addi sp, sp, 16
        ret
        .size bench_entry, . - bench_entry
        .option pop
    .endm

#endif

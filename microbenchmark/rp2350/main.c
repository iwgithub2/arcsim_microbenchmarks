/* RP2350/Pico SDK entry point for the shared assembly microbenchmarks. */
#include <stdint.h>
#include <stdio.h>

#include "hardware/clocks.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

#ifndef PICO_RISCV
#include "dwt.h"
#endif

extern void bench_entry(void);
extern const char microbench_name[];
extern const char microbench_implementation[];

#ifndef INNER_REPS
#error "INNER_REPS must be defined"
#endif

volatile uint32_t _inner_delta_cyc[INNER_REPS];

#ifdef PICO_RISCV
static inline void riscv_cycle_counter_enable(void)
{
    /* Hazard3 implements the standard machine cycle counter. Clear CY in
     * mcountinhibit, then reset the high/low halves before measurement. */
    __asm__ volatile(
        "csrci mcountinhibit, 1\n"
        "csrw mcycleh, zero\n"
        "csrw mcycle, zero\n"
        ::: "memory");
}
#endif

int main(void)
{
    /* stdio_init_all() intentionally waits until a Mac USB-serial client
     * connects (configured in CMake), so the one-shot results are not lost
     * while the CDC device is still enumerating after a flash/reboot. */
    stdio_init_all();

#ifndef PICO_RISCV
    dwt_enable();
#else
    riscv_cycle_counter_enable();
#endif

    /* pico_stdio_usb uses USB interrupts. Keep them out of the DWT window;
     * restore them before printing the captured values. */
    uint32_t irq_state = save_and_disable_interrupts();
    bench_entry();
    restore_interrupts(irq_state);

    printf("MICROBENCH_BOARD board=%s core=%s core_id=%u active_cores=1 "
           "implementation=%s sys_hz=%lu reps=%u\n",
           PICO_BOARD,
#ifdef PICO_RISCV
           "hazard3",
#else
           "cortex-m33",
#endif
           get_core_num(), microbench_implementation,
           (unsigned long)clock_get_hz(clk_sys),
           (unsigned)INNER_REPS);
    for (uint32_t i = 0; i < INNER_REPS; ++i) {
        printf("MICROBENCH name=%s rep=%lu inner=%lu\n",
               microbench_name, (unsigned long)i,
               (unsigned long)_inner_delta_cyc[i]);
    }
    stdio_flush();

    /* Stay alive so `picotool reboot -u -f` can put SDK-compatible firmware
     * back into BOOTSEL mode without unplugging the board. */
    for (;;) {
        tight_loop_contents();
    }
}

/* Standalone RP2350 XIP cache path probe; all timed wrappers execute in SRAM. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "dwt.h"
#include "hardware/clocks.h"
#include "hardware/sync.h"
#include "hardware/xip_cache.h"
#include "pico/stdlib.h"

extern uint32_t xip_add32(void);
extern uint32_t xip_load(const uint32_t *address);
extern void xip_chain_load(const uint32_t *address);
extern void conflict2(const uint32_t *a, const uint32_t *b);
extern void conflict3(const uint32_t *a, const uint32_t *b, const uint32_t *c);
extern void mixed_sram(const uint32_t *address,
                       uint32_t (*load)(const uint32_t *));
extern const uint32_t xip_data_a, xip_data_b, xip_data_c;
extern const uint32_t xip_bank_data[4];

static volatile uint32_t result_sink;

static uint32_t __no_inline_not_in_flash_func(measure_add32)(bool cold) {
    if (cold) {
        xip_cache_invalidate_all();
    } else {
        result_sink = xip_add32();
        result_sink = xip_add32();
    }
    __asm__ volatile("dsb\n isb" ::: "memory");
    uint32_t start = DWT_CYCCNT;
    uint32_t value = xip_add32();
    uint32_t elapsed = DWT_CYCCNT - start;
    result_sink = value;
    return elapsed;
}

static uint32_t __no_inline_not_in_flash_func(measure_conflict)(bool three) {
    xip_cache_invalidate_all();
    if (three) {
        conflict3(&xip_data_a, &xip_data_b, &xip_data_c);
        conflict3(&xip_data_a, &xip_data_b, &xip_data_c);
    } else {
        conflict2(&xip_data_a, &xip_data_b);
        conflict2(&xip_data_a, &xip_data_b);
    }
    __asm__ volatile("dsb\n isb" ::: "memory");
    uint32_t start = DWT_CYCCNT;
    if (three) {
        conflict3(&xip_data_a, &xip_data_b, &xip_data_c);
    } else {
        conflict2(&xip_data_a, &xip_data_b);
    }
    return DWT_CYCCNT - start;
}

static uint32_t __no_inline_not_in_flash_func(measure_mixed)(
    const uint32_t *address) {
    xip_cache_invalidate_all();
    mixed_sram(address, xip_load);
    mixed_sram(address, xip_load);
    __asm__ volatile("dsb\n isb" ::: "memory");
    uint32_t start = DWT_CYCCNT;
    mixed_sram(address, xip_load);
    return DWT_CYCCNT - start;
}

static uint32_t __no_inline_not_in_flash_func(measure_chain)(
    const uint32_t *address) {
    xip_cache_invalidate_all();
    xip_chain_load(address);
    xip_chain_load(address);
    __asm__ volatile("dsb\n isb" ::: "memory");
    uint32_t start = DWT_CYCCNT;
    xip_chain_load(address);
    return DWT_CYCCNT - start;
}

int main(void) {
    set_sys_clock_khz(150000, true);
    stdio_init_all();
    dwt_enable();
    *(volatile uint32_t *)0xe000e008u |= 1u << 2; /* ACTLR.DISFOLD */
    __asm__ volatile("dsb\n isb" ::: "memory");
    uintptr_t code = (uintptr_t)xip_load & ~(uintptr_t)1;
    const uint32_t *bank0 = &xip_bank_data[0];
    const uint32_t *bank1 = &xip_bank_data[2];
    const uint32_t *same = (code & 8u) ? bank1 : bank0;
    const uint32_t *opposite = (code & 8u) ? bank0 : bank1;
    printf("CACHE_PROBE_HEADER sys_hz=%lu disfold=%lu code=0x%08lx chain=0x%08lx a=0x%08lx b=0x%08lx c=0x%08lx same=0x%08lx opposite=0x%08lx\n",
           (unsigned long)clock_get_hz(clk_sys),
           (unsigned long)((*(volatile uint32_t *)0xe000e008u >> 2) & 1u),
           (unsigned long)code,
           (unsigned long)((uintptr_t)xip_chain_load & ~(uintptr_t)1),
           (unsigned long)(uintptr_t)&xip_data_a,
           (unsigned long)(uintptr_t)&xip_data_b,
           (unsigned long)(uintptr_t)&xip_data_c,
           (unsigned long)(uintptr_t)same,
           (unsigned long)(uintptr_t)opposite);
    for (unsigned rep = 0; rep < 20; ++rep) {
        uint32_t irq = save_and_disable_interrupts();
        uint32_t cold = measure_add32(true);
        uint32_t warm = measure_add32(false);
        uint32_t two = measure_conflict(false);
        uint32_t three = measure_conflict(true);
        uint32_t same_bank = measure_mixed(same);
        uint32_t other_bank = measure_mixed(opposite);
        uint32_t chain_same = measure_chain(bank0);
        uint32_t chain_opposite = measure_chain(bank1);
        restore_interrupts(irq);
        printf("CACHE_PROBE rep=%u cold_add32=%lu warm_add32=%lu conflict2=%lu conflict3=%lu mixed_same=%lu mixed_opposite=%lu chain_same=%lu chain_opposite=%lu\n",
               rep, (unsigned long)cold, (unsigned long)warm,
               (unsigned long)two, (unsigned long)three,
               (unsigned long)same_bank, (unsigned long)other_bank,
               (unsigned long)chain_same, (unsigned long)chain_opposite);
        stdio_flush();
    }
    for (;;) tight_loop_contents();
}

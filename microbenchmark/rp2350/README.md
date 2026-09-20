# RP2350 (Pico SDK) microbenchmarks

This directory builds single-core microbenchmarks for both processor
architectures in RP2350:

- 184 Arm kernels on Cortex-M33, including the 128 VFP cases;
- 56 native integer kernels on Hazard3 RISC-V.

Hazard3 has no floating-point unit, so its sweep deliberately excludes the
128 Arm VFP kernels instead of substituting software floating point. Arm uses
`DWT_CYCCNT`; Hazard3 uses the standard RISC-V `mcycle` CSR.

Both targets are hard-configured for one active core. `PICO_CORE1_STACK_SIZE`
is zero, a post-link check rejects firmware containing any core-1 launch path,
and every physical result must report `core_id=0 active_cores=1` or the host
sweep stops.

The default board is Raspberry Pi Pico 2 (`PICO_BOARD=pico2`). For Pico 2 W,
use `-DPICO_BOARD=pico2_w`. For a third-party RP2350 board, use its board name
from `pico-sdk/src/boards/include/boards/`.

## Configure and build

From this directory:

On the Mac where this repository was prepared, the SDK and locally unpacked
Arm toolchain are already wired into a preset:

```sh
cmake --preset mac-pico2-local
cmake --build --preset mac-pico2-local --target bench_nop16_100
```

For a different checkout layout, configure the paths explicitly:

```sh
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DPICO_SDK_PATH=/absolute/path/to/pico-sdk \
  -DPICO_TOOLCHAIN_PATH=/absolute/path/to/arm-gnu-toolchain \
  -DPICO_BOARD=pico2
cmake --build build --target bench_nop16_100
```

The flashable file is `build/bench_nop16_100.uf2`.

## Hazard3 toolchain and build

On the prepared Mac, Raspberry Pi's RISC-V GCC toolchain is unpacked at
`../../../toolchains/riscv-toolchain-16` and used by these presets:

```sh
cmake --preset mac-pico2-riscv-local
cmake --build --preset mac-pico2-riscv-local --target bench_nop16_100
```

The resulting UF2 is a RISC-V image. Confirm it before flashing with:

```sh
picotool info build-riscv/bench_nop16_100.uf2
```

The 56 individual sources are stored under `../kernels/riscv/alu`, mirroring
the filenames under `../kernels/alu`. They include the shared
`riscv/harness.h` measurement wrapper. `nop16` and `add16` use compressed
16-bit instructions, `add32` forces a 32-bit encoding, and the Arm `umull`
operation maps to the RV32 `mul` + `mulhu` pair.

Regenerate the complete checked-in source set with:

```sh
python3 riscv/generate_kernel.py \
  --all-from ../kernels/alu \
  --output-dir ../kernels/riscv/alu
```

## First flash on macOS

1. Use a data-capable USB cable.
2. Hold **BOOTSEL** while plugging in the board, then release it. A Pico 2
   appears in Finder as a volume named `RP2350` and `picotool info -a` should
   identify it.
3. Flash and capture the benchmark:

```sh
python3 run_benchmark.py bench_nop16_100
```

The script uses `picotool` to force an SDK application into BOOTSEL when
possible, verify and execute the UF2, wait for the USB CDC
serial port, captures all repetitions, and prints min/average/max cycles.

After this SDK-built firmware is installed, later runs generally do not need
the BOOTSEL button: put it back in boot mode with `picotool reboot -u -f`, then
run the script again. If forced reboot cannot see the application, use BOOTSEL.

You can also drag the UF2 onto the `RP2350` volume. Open the USB serial port
after reboot (for example with `picocom -b 115200 /dev/cu.usbmodem*`); firmware
waits for a serial client before measuring and printing.

## Measurement notes

- RP2350 and STM32G474 are different microarchitectures and flash/cache
  systems. Compare instruction throughput, but do not interpret the RP2350
  numbers using the STM32-specific `N + 6` framing claim.
- USB interrupts are disabled during the warmup and measured repetitions, then
  restored before printing.
- The Pico SDK default clock is reported in the `MICROBENCH_BOARD` line. Do not
  compare runs made at different `sys_hz` values.
- This port currently keeps the code in the SDK's normal XIP flash layout; it
  does not reproduce the STM32 port's fixed flash addresses or its four
  STM32-specific ART cache/prefetch variants.

## Automated physical sweeps

Keep the Pico 2 connected and run:

```sh
python3 run_all_benchmarks.py
```

Run all 56 native Hazard3 integer kernels with:

```sh
python3 run_all_benchmarks.py --architecture riscv
```

Requesting `--architecture riscv --category fp` is an error by design.

The sweep uses a single incremental `rp2350_benchmark` target. The Pico SDK is
compiled once; each iteration then changes only the selected shared kernel and
its embedded name, relinks, updates changed flash sectors, captures USB CDC,
and immediately persists the result. A timestamped directory under `results/`
contains:

- `summary.csv` — one row per kernel with min/mean/median/max/stdev cycles;
- `steady_state_summary.csv` — the same core statistics over reps 1..9,
  plus rep 0 and its penalty relative to the steady-state median;
- `repetitions.csv` — all 10 raw cycle samples per kernel;
- `errors.csv` — every failed attempt, including retries;
- `logs/<category>/<benchmark>.log` — build, flash, and raw serial output;
- `manifest.json` — tool versions, repository revision, host, and kernel list.
- `single_core_evidence.txt` — link-time and runtime proof that only core 0 ran.

To resume an interrupted run, pass its existing directory:

```sh
python3 run_all_benchmarks.py --output-dir results/rp2350-arm-<timestamp>
```

Useful validation/subset forms:

```sh
python3 run_all_benchmarks.py --dry-run
python3 run_all_benchmarks.py --limit 3
python3 run_all_benchmarks.py --category alu
python3 run_all_benchmarks.py --bench bench_nop16_100
```

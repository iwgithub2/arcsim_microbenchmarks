#!/usr/bin/env bash
# Build new isolation ELFs without modifying or overwriting the completed study.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 NEW_OUTPUT_DIRECTORY" >&2
    exit 2
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
microbench_dir=$(cd "${script_dir}/.." && pwd)
output_dir=$1
if [[ -e "${output_dir}" ]]; then
    echo "refusing to overwrite existing path: ${output_dir}" >&2
    exit 2
fi
mkdir -p "${output_dir}/gem5" "${output_dir}/stm32-flash" \
    "${output_dir}/rp2350-objects"

base_flags=(
    -mthumb -Wall -Wextra -fno-builtin -fno-common -DINNER_REPS=10
    -I"${microbench_dir}/include" -I"${microbench_dir}/board/stm32g474re"
)
m4_flags=(-mcpu=cortex-m4 -mfloat-abi=hard -mfpu=fpv4-sp-d16)
c_flags=(-std=c11 -Os -O3 -DNDEBUG)
link_flags=(
    "${m4_flags[@]}" -nostdlib -nostartfiles
    -T "${microbench_dir}/board/stm32g474re/stm32g474re.ld"
    -Wl,--no-warn-rwx-segments
)

build_common() {
    local platform=$1 out=$2
    local platform_flags=()
    if [[ ${platform} == gem5 ]]; then
        platform_flags=(-DPLATFORM_GEM5)
    else
        platform_flags=(-DPLATFORM_HARDWARE -DENABLE_ICACHE=1 \
            -DENABLE_DCACHE=1 -DENABLE_PREFETCH=1)
    fi
    for source in startup system_init flash_config; do
        arm-none-eabi-gcc "${base_flags[@]}" "${m4_flags[@]}" \
            "${platform_flags[@]}" "${c_flags[@]}" \
            -c "${microbench_dir}/src/${source}.c" -o "${out}/${source}.o"
    done
}

build_one() {
    local platform=$1 out=$2 benchmark=$3 source=$4 iso_n=${5:-}
    local platform_flags=()
    if [[ ${platform} == gem5 ]]; then
        platform_flags=(-DPLATFORM_GEM5)
    else
        platform_flags=(-DPLATFORM_HARDWARE -DENABLE_ICACHE=1 \
            -DENABLE_DCACHE=1 -DENABLE_PREFETCH=1)
    fi
    local extra=()
    if [[ -n ${iso_n} ]]; then extra=(-DISO_N="${iso_n}"); fi
    arm-none-eabi-gcc "${base_flags[@]}" "${m4_flags[@]}" \
        "${platform_flags[@]}" ${extra[@]+"${extra[@]}"} -O3 -DNDEBUG \
        -c "${source}" -o "${out}/${benchmark}.kernel.o"
    arm-none-eabi-gcc "${base_flags[@]}" "${m4_flags[@]}" \
        "${platform_flags[@]}" "${c_flags[@]}" \
        -DBENCH_NAME=\"${benchmark}\" -c "${microbench_dir}/src/main.c" \
        -o "${out}/${benchmark}.main.o"
    arm-none-eabi-gcc "${link_flags[@]}" \
        -Wl,-Map="${out}/${benchmark}.map" \
        "${out}/${benchmark}.kernel.o" "${out}/startup.o" \
        "${out}/system_init.o" "${out}/flash_config.o" \
        "${out}/${benchmark}.main.o" -o "${out}/${benchmark}.elf"
}

build_common gem5 "${output_dir}/gem5"
build_common hardware "${output_dir}/stm32-flash"

for platform in gem5 hardware; do
    out=${output_dir}/gem5
    [[ ${platform} == hardware ]] && out=${output_dir}/stm32-flash
    for n in 0 1 2 4 8 16 32 64 100; do
        build_one "${platform}" "${out}" "bench_iso_nop16_${n}" \
            "${script_dir}/bench_iso_nop16.S" "${n}"
    done
    for name in forward_taken call_indirect jump_indirect; do
        build_one "${platform}" "${out}" "bench_iso_${name}_1" \
            "${script_dir}/bench_iso_${name}_1.S"
    done
    # Matched six-family physical-M4 pilot.  These use the same assembly
    # sources as gem5; only PLATFORM_HARDWARE changes the ROI mechanism.
    for family in baseline_nop16 conditional_taken conditional_not_taken \
                  call_direct call_indirect jump_indirect; do
        for n in 8 100; do
            build_one "${platform}" "${out}" "bench_cf_${family}_${n}" \
                "${microbench_dir}/kernels/control/bench_cf_${family}_${n}.S"
        done
    done
done

# The RP2350 SDK and measured linker layout are unavailable on this host.
# Object-only builds still test whether the same source has the same kernel bytes.
for source in "${script_dir}"/bench_iso_*.S \
              "${microbench_dir}"/kernels/control/bench_cf_baseline_nop16_{8,100}.S \
              "${microbench_dir}"/kernels/control/bench_cf_conditional_taken_{8,100}.S \
              "${microbench_dir}"/kernels/control/bench_cf_conditional_not_taken_{8,100}.S \
              "${microbench_dir}"/kernels/control/bench_cf_call_direct_{8,100}.S \
              "${microbench_dir}"/kernels/control/bench_cf_call_indirect_{8,100}.S \
              "${microbench_dir}"/kernels/control/bench_cf_jump_indirect_{8,100}.S; do
    stem=$(basename "${source}" .S)
    extra=()
    case ${stem} in
        bench_iso_nop16)
            extra=(-DISO_N=0)
            stem=${stem}_0
            ;;
        bench_iso_load_same|bench_iso_load_other)
            extra=(-DISO_N=8)
            stem=${stem}_8
            ;;
    esac
    arm-none-eabi-gcc -mcpu=cortex-m33 -mthumb -mfloat-abi=hard \
        -mfpu=fpv5-sp-d16 -Wall -Wextra -fno-builtin -fno-common \
        -DPLATFORM_HARDWARE -DBOARD_RP2350 -DINNER_REPS=10 \
        -I"${microbench_dir}/include" -I"${microbench_dir}/rp2350" \
        ${extra[@]+"${extra[@]}"} -O3 -DNDEBUG \
        -c "${source}" -o "${output_dir}/rp2350-objects/${stem}.o"
done

for out in gem5 stm32-flash rp2350-objects; do
    sha256sum "${output_dir}/${out}"/* > "${output_dir}/${out}/sha256sums.txt"
done  

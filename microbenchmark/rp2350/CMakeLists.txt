cmake_minimum_required(VERSION 3.20)

# RP2350 support uses the official Pico SDK for startup, clock, USB stdio,
# boot metadata, and UF2 generation. Point PICO_SDK_PATH at a 2.x SDK checkout.
if(NOT DEFINED PICO_SDK_PATH)
    if(DEFINED ENV{PICO_SDK_PATH})
        set(PICO_SDK_PATH "$ENV{PICO_SDK_PATH}")
    else()
        message(FATAL_ERROR
            "Set PICO_SDK_PATH to the pico-sdk checkout, e.g. "
            "cmake -S . -B build -DPICO_SDK_PATH=/path/to/pico-sdk")
    endif()
endif()

set(PICO_BOARD pico2 CACHE STRING
    "Pico SDK board name (pico2, pico2_w, or another RP2350 board)")
set(PICO_PLATFORM rp2350 CACHE STRING "Pico SDK target platform")
# This suite is deliberately single-core. A zero core-1 stack makes an
# accidental SDK multicore launch fail at build/runtime instead of silently
# changing the measurement environment.
set(PICO_CORE1_STACK_SIZE 0 CACHE STRING "Disable the unused second core" FORCE)

# Wait until a host opens the USB CDC port before running. This makes a
# flash-and-capture run reliable: no benchmark lines disappear during USB
# enumeration immediately after reboot.
set(PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS -1 CACHE STRING "" FORCE)
set(PICO_STDIO_USB_POST_CONNECT_WAIT_DELAY_MS 100 CACHE STRING "" FORCE)

include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")

project(microbenchmark_rp2350 C CXX ASM)
pico_sdk_init()

find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(INNER_REPS 10 CACHE STRING "Measured repetitions per firmware run")
set(RP2350_BENCH "" CACHE STRING
    "Build one selected kernel as the incremental rp2350_benchmark target")
set(MICROBENCH_ROOT "${CMAKE_CURRENT_LIST_DIR}/..")

file(GLOB ALU_KERNELS CONFIGURE_DEPENDS
    "${MICROBENCH_ROOT}/kernels/alu/bench_*.S")
file(GLOB FP_KERNELS CONFIGURE_DEPENDS
    "${MICROBENCH_ROOT}/kernels/fp/bench_*.S")
file(GLOB RISCV_ALU_KERNELS CONFIGURE_DEPENDS
    "${MICROBENCH_ROOT}/kernels/riscv/alu/bench_*.S")
if(PICO_RISCV)
    # Hazard3 in RP2350 implements no RISC-V F extension. Keep this an
    # instruction microbenchmark suite by excluding, rather than emulating,
    # the Arm VFP cases.
    list(LENGTH RISCV_ALU_KERNELS riscv_kernel_count)
    if(NOT riscv_kernel_count EQUAL 56)
        message(FATAL_ERROR
            "Expected 56 materialized Hazard3 kernels, found ${riscv_kernel_count}. "
            "Run riscv/generate_kernel.py --all-from ../kernels/alu "
            "--output-dir ../kernels/riscv/alu")
    endif()
    set(ALL_KERNELS ${RISCV_ALU_KERNELS})
else()
    set(ALL_KERNELS ${ALU_KERNELS} ${FP_KERNELS})
endif()

function(add_rp2350_benchmark target bench kernel_source)
    # Keep the benchmark name in one tiny generated translation unit rather
    # than a target-wide compile definition. During a physical sweep this lets
    # CMake reuse all Pico SDK objects when RP2350_BENCH changes; only the
    # kernel, this name object, and the final link need updating.
    set(name_source "${CMAKE_CURRENT_BINARY_DIR}/generated/${target}_name.c")
    if(PICO_RISCV)
        set(implementation "native-riscv")
    else()
        set(implementation "native-arm")
    endif()
    file(GENERATE OUTPUT "${name_source}"
        CONTENT "const char microbench_name[] = \"${bench}\";\nconst char microbench_implementation[] = \"${implementation}\";\n")

    add_executable(${target}
        "${kernel_source}"
        "${CMAKE_CURRENT_LIST_DIR}/main.c"
        "${name_source}"
    )

    target_include_directories(${target} PRIVATE
        "${MICROBENCH_ROOT}/include"
        "${CMAKE_CURRENT_LIST_DIR}"
    )
    target_compile_definitions(${target} PRIVATE
        PLATFORM_HARDWARE
        BOARD_RP2350
        PICO_CORE1_STACK_SIZE=0
        INNER_REPS=${INNER_REPS}
    )
    target_compile_options(${target} PRIVATE
        -Wall
        -Wextra
        -fno-common
    )
    target_link_libraries(${target} PRIVATE
        pico_stdlib
        pico_stdio_usb
        hardware_clocks
        hardware_sync
    )

    pico_enable_stdio_usb(${target} 1)
    pico_enable_stdio_uart(${target} 0)
    if(target STREQUAL "rp2350_benchmark")
        # Stable metadata avoids recompiling all SDK sources when the selected
        # kernel changes during a sweep. The exact kernel name is still emitted
        # in the USB result and recorded by the host-side collector.
        pico_set_program_name(${target} "rp2350-microbenchmark-sweep")
        pico_set_program_description(${target}
            "Incremental RP2350 physical microbenchmark sweep")
    else()
        pico_set_program_name(${target} "rp2350-${bench}")
        pico_set_program_description(${target}
            "RP2350 cycle microbenchmark: ${bench}")
    endif()
    pico_add_extra_outputs(${target})
    add_custom_command(TARGET ${target} POST_BUILD
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_LIST_DIR}/verify_single_core.py"
            --nm "${CMAKE_NM}"
            --elf "$<TARGET_FILE:${target}>"
            --output "${CMAKE_CURRENT_BINARY_DIR}/${target}.single-core.txt"
        VERBATIM
        COMMENT "Verifying ${target} has no core-1 launch path")
endfunction()

if(RP2350_BENCH)
    set(selected_source "")
    foreach(kernel_source IN LISTS ALL_KERNELS)
        get_filename_component(bench "${kernel_source}" NAME_WE)
        if(bench STREQUAL RP2350_BENCH)
            set(selected_source "${kernel_source}")
            break()
        endif()
    endforeach()
    if(NOT selected_source)
        message(FATAL_ERROR "Unknown RP2350_BENCH: ${RP2350_BENCH}")
    endif()
    message(STATUS "Incremental RP2350 sweep kernel: ${RP2350_BENCH}")
    add_rp2350_benchmark(
        rp2350_benchmark "${RP2350_BENCH}" "${selected_source}")
else()
    foreach(kernel_source IN LISTS ALL_KERNELS)
        get_filename_component(bench "${kernel_source}" NAME_WE)
        add_rp2350_benchmark("${bench}" "${bench}" "${kernel_source}")
    endforeach()
endif()

cmake_minimum_required(VERSION 3.20)

if(NOT DEFINED PICO_SDK_PATH)
    message(FATAL_ERROR "Set PICO_SDK_PATH to a Pico SDK 2.x checkout")
endif()
set(PICO_BOARD pico2 CACHE STRING "RP2350 board")
set(PICO_PLATFORM rp2350 CACHE STRING "Use the Cortex-M33")
set(PICO_CORE1_STACK_SIZE 0 CACHE STRING "Single-core probe" FORCE)
set(PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS -1 CACHE STRING "" FORCE)
include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")

project(rp2350_cache_probe C CXX ASM)
pico_sdk_init()

add_executable(rp2350_cache_probe main.c probe_kernels.S)
target_include_directories(rp2350_cache_probe PRIVATE ..)
target_link_libraries(rp2350_cache_probe PRIVATE
    pico_stdlib pico_stdio_usb hardware_clocks hardware_sync hardware_xip_cache)
target_compile_options(rp2350_cache_probe PRIVATE -O2 -Wall -Wextra)
pico_enable_stdio_usb(rp2350_cache_probe 1)
pico_enable_stdio_uart(rp2350_cache_probe 0)
pico_add_extra_outputs(rp2350_cache_probe)

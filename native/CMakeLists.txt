cmake_minimum_required(VERSION 3.16)
project(omarchy_hardware_core LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(omarchy_hardware_core STATIC src/hardware_core.c)
target_include_directories(omarchy_hardware_core PUBLIC include)
target_compile_options(omarchy_hardware_core PRIVATE -Wall -Wextra -Wpedantic)

enable_testing()
add_executable(hardware_core_test tests/hardware_core_test.c)
target_link_libraries(hardware_core_test PRIVATE omarchy_hardware_core)
target_compile_options(hardware_core_test PRIVATE -Wall -Wextra -Wpedantic)
add_test(NAME hardware_core_test COMMAND hardware_core_test)

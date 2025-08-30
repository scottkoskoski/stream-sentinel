# CMakeLists.txt for Stream-Sentinel High-Performance C++ Inference Engine
# Professional-grade build system for FAANG-level quality
cmake_minimum_required(VERSION 3.18)

project(stream_sentinel_cpp
    VERSION 2.0.0
    DESCRIPTION "High-performance C++ inference engine for Stream-Sentinel fraud detection"
    LANGUAGES CXX C
)

# =============================================================================
# Build Configuration
# =============================================================================

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Build type configuration
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Choose build type" FORCE)
    set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS "Debug" "Release" "RelWithDebInfo" "MinSizeRel")
endif()

message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")

# =============================================================================
# Compiler and Platform Detection
# =============================================================================

# Detect compiler and set appropriate flags
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    set(UNIX_LIKE_COMPILER TRUE)
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
    set(MSVC_COMPILER TRUE)
endif()

# Platform-specific configurations
if(WIN32)
    add_definitions(-DWIN32_LEAN_AND_MEAN -DNOMINMAX)
elseif(APPLE)
    set(CMAKE_MACOSX_RPATH ON)
endif()

# =============================================================================
# Compiler Flags and Optimizations
# =============================================================================

# Base compiler flags
if(UNIX_LIKE_COMPILER)
    # GCC/Clang flags
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra -Wno-unused-parameter")
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -fPIC -pthread")
    
    # Release optimizations
    set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG -march=native -mtune=native")
    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} -ffast-math -funroll-loops")
    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} -flto -fuse-linker-plugin")
    
    # Debug flags
    set(CMAKE_CXX_FLAGS_DEBUG "-O0 -g -DDEBUG -fno-omit-frame-pointer")
    set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} -fsanitize=address -fsanitize=undefined")
    
    # RelWithDebInfo flags
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O2 -g -DNDEBUG")
    
elseif(MSVC_COMPILER)
    # MSVC flags
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /W3 /MP")
    set(CMAKE_CXX_FLAGS_RELEASE "/O2 /Ob2 /DNDEBUG /GL")
    set(CMAKE_CXX_FLAGS_DEBUG "/Od /Zi /DDEBUG")
    
    # Enable advanced instruction sets on MSVC
    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} /arch:AVX2")
endif()

# =============================================================================
# SIMD Instruction Set Detection and Configuration
# =============================================================================

# Check for SIMD support
include(CheckCXXCompilerFlag)

if(UNIX_LIKE_COMPILER)
    # Check for AVX2 support
    check_cxx_compiler_flag("-mavx2" HAS_AVX2_FLAG)
    if(HAS_AVX2_FLAG)
        set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -mavx2")
        add_definitions(-DHAS_AVX2_SUPPORT=1)
        message(STATUS "AVX2 support enabled")
    endif()
    
    # Check for FMA support
    check_cxx_compiler_flag("-mfma" HAS_FMA_FLAG)
    if(HAS_FMA_FLAG)
        set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -mfma")
        add_definitions(-DHAS_FMA_SUPPORT=1)
        message(STATUS "FMA support enabled")
    endif()
    
    # Check for AVX-512 support (optional)
    check_cxx_compiler_flag("-mavx512f" HAS_AVX512_FLAG)
    if(HAS_AVX512_FLAG)
        set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -mavx512f")
        add_definitions(-DHAS_AVX512_SUPPORT=1)
        message(STATUS "AVX-512 support enabled")
    endif()
endif()

# =============================================================================
# Dependencies
# =============================================================================

# Find required packages
find_package(PkgConfig REQUIRED)

# XGBoost dependency
find_path(XGBOOST_INCLUDE_DIR
    NAMES xgboost/c_api.h
    PATHS 
        /usr/include
        /usr/local/include
        ${CMAKE_PREFIX_PATH}/include
)

find_library(XGBOOST_LIBRARY
    NAMES xgboost
    PATHS
        /usr/lib
        /usr/local/lib
        ${CMAKE_PREFIX_PATH}/lib
)

if(NOT XGBOOST_INCLUDE_DIR OR NOT XGBOOST_LIBRARY)
    message(FATAL_ERROR "XGBoost not found. Please install XGBoost development packages.")
endif()

message(STATUS "XGBoost include: ${XGBOOST_INCLUDE_DIR}")
message(STATUS "XGBoost library: ${XGBOOST_LIBRARY}")

# Python and pybind11 for Python bindings
find_package(Python3 COMPONENTS Interpreter Development NumPy REQUIRED)
find_package(pybind11 REQUIRED)

message(STATUS "Python version: ${Python3_VERSION}")
message(STATUS "Python executable: ${Python3_EXECUTABLE}")
message(STATUS "NumPy include: ${Python3_NumPy_INCLUDE_DIRS}")

# Threading support
find_package(Threads REQUIRED)

# =============================================================================
# Include Directories
# =============================================================================

include_directories(${CMAKE_CURRENT_SOURCE_DIR})
include_directories(${XGBOOST_INCLUDE_DIR})
include_directories(${Python3_INCLUDE_DIRS})
include_directories(${Python3_NumPy_INCLUDE_DIRS})

# =============================================================================
# Source Files
# =============================================================================

set(CORE_SOURCES
    xgboost_engine.cpp
    feature_processor.cpp
    memory_pool.cpp
)

set(CORE_HEADERS
    xgboost_engine.hpp
    feature_processor.hpp
    memory_pool.hpp
)

# =============================================================================
# Core Library Target
# =============================================================================

# Create core static library
add_library(stream_sentinel_core STATIC ${CORE_SOURCES} ${CORE_HEADERS})

target_link_libraries(stream_sentinel_core
    ${XGBOOST_LIBRARY}
    Threads::Threads
)

# Set library properties
set_target_properties(stream_sentinel_core PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
)

# =============================================================================
# Python Extension Module
# =============================================================================

# Create Python extension module
pybind11_add_module(stream_sentinel_cpp python_bindings.cpp)

target_link_libraries(stream_sentinel_cpp PRIVATE
    stream_sentinel_core
    ${XGBOOST_LIBRARY}
    pybind11::module
)

# Set module properties
set_target_properties(stream_sentinel_cpp PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    INTERPROCEDURAL_OPTIMIZATION ON
)

# Compiler-specific optimizations for Python module
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(stream_sentinel_cpp PRIVATE
        -fvisibility=hidden
        -flto
    )
    target_link_options(stream_sentinel_cpp PRIVATE
        -flto
        -Wl,--strip-all
    )
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    target_compile_options(stream_sentinel_cpp PRIVATE
        -fvisibility=hidden
        -flto=thin
    )
    target_link_options(stream_sentinel_cpp PRIVATE
        -flto=thin
        -Wl,-dead_strip
    )
endif()

# =============================================================================
# Benchmarking and Testing
# =============================================================================

option(BUILD_BENCHMARKS "Build benchmark executables" ON)
option(BUILD_TESTS "Build test executables" ON)

if(BUILD_BENCHMARKS)
    add_executable(inference_benchmark
        benchmarks/inference_benchmark.cpp
    )
    
    target_link_libraries(inference_benchmark
        stream_sentinel_core
        ${XGBOOST_LIBRARY}
        Threads::Threads
    )
    
    # Add performance benchmark
    add_executable(simd_benchmark
        benchmarks/simd_benchmark.cpp
    )
    
    target_link_libraries(simd_benchmark
        stream_sentinel_core
        Threads::Threads
    )
endif()

if(BUILD_TESTS)
    # Find or build a simple testing framework
    add_executable(unit_tests
        tests/test_xgboost_engine.cpp
        tests/test_feature_processor.cpp
        tests/test_memory_pool.cpp
    )
    
    target_link_libraries(unit_tests
        stream_sentinel_core
        ${XGBOOST_LIBRARY}
        Threads::Threads
    )
    
    # Enable testing
    enable_testing()
    add_test(NAME unit_tests COMMAND unit_tests)
endif()

# =============================================================================
# Installation
# =============================================================================

# Install Python module
install(TARGETS stream_sentinel_cpp
    LIBRARY DESTINATION ${Python3_SITEARCH}
    RUNTIME DESTINATION ${Python3_SITEARCH}
)

# Install headers for C++ development
install(FILES ${CORE_HEADERS}
    DESTINATION include/stream_sentinel
)

# Install core library
install(TARGETS stream_sentinel_core
    LIBRARY DESTINATION lib
    ARCHIVE DESTINATION lib
)

# =============================================================================
# Package Configuration
# =============================================================================

# Create pkg-config file
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/stream_sentinel.pc.in
    ${CMAKE_CURRENT_BINARY_DIR}/stream_sentinel.pc
    @ONLY
)

install(FILES ${CMAKE_CURRENT_BINARY_DIR}/stream_sentinel.pc
    DESTINATION lib/pkgconfig
)

# =============================================================================
# Custom Targets
# =============================================================================

# Add a target to run benchmarks
if(BUILD_BENCHMARKS)
    add_custom_target(run_benchmarks
        COMMAND inference_benchmark
        COMMAND simd_benchmark
        DEPENDS inference_benchmark simd_benchmark
        COMMENT "Running performance benchmarks"
    )
endif()

# Add a target to run tests
if(BUILD_TESTS)
    add_custom_target(run_tests
        COMMAND ${CMAKE_CTEST_COMMAND} --verbose
        DEPENDS unit_tests
        COMMENT "Running unit tests"
    )
endif()

# Add a target for code formatting (if clang-format is available)
find_program(CLANG_FORMAT clang-format)
if(CLANG_FORMAT)
    add_custom_target(format
        COMMAND ${CLANG_FORMAT} -i ${CORE_SOURCES} ${CORE_HEADERS} python_bindings.cpp
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        COMMENT "Formatting source code"
    )
endif()

# =============================================================================
# Status Summary
# =============================================================================

message(STATUS "")
message(STATUS "Stream-Sentinel C++ Inference Engine Configuration Summary:")
message(STATUS "  Version: ${PROJECT_VERSION}")
message(STATUS "  Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "  Compiler: ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
message(STATUS "  Python: ${Python3_VERSION}")
message(STATUS "  XGBoost: Found")
message(STATUS "  SIMD support: ${HAS_AVX2_FLAG}")
message(STATUS "  Build benchmarks: ${BUILD_BENCHMARKS}")
message(STATUS "  Build tests: ${BUILD_TESTS}")
message(STATUS "  Install prefix: ${CMAKE_INSTALL_PREFIX}")
message(STATUS "")

# =============================================================================
# Validation
# =============================================================================

# Ensure all required components are available
if(NOT TARGET stream_sentinel_cpp)
    message(FATAL_ERROR "Failed to create Python extension module target")
endif()

if(NOT TARGET stream_sentinel_core)
    message(FATAL_ERROR "Failed to create core library target")
endif()

# Final configuration check
get_target_property(FINAL_COMPILE_FLAGS stream_sentinel_cpp COMPILE_OPTIONS)
message(STATUS "Final compile flags: ${FINAL_COMPILE_FLAGS}")

# Add compile definitions for version info
target_compile_definitions(stream_sentinel_core PRIVATE
    STREAM_SENTINEL_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
    STREAM_SENTINEL_VERSION_MINOR=${PROJECT_VERSION_MINOR}
    STREAM_SENTINEL_VERSION_PATCH=${PROJECT_VERSION_PATCH}
)

target_compile_definitions(stream_sentinel_cpp PRIVATE
    STREAM_SENTINEL_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
    STREAM_SENTINEL_VERSION_MINOR=${PROJECT_VERSION_MINOR}
    STREAM_SENTINEL_VERSION_PATCH=${PROJECT_VERSION_PATCH}
)
# Copyright 2026 Shaka Decryptor Contributors. All rights reserved.
#
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.

cmake_minimum_required(VERSION 3.24)
project(shaka-decryptor VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Dependency: Shaka Packager (via git submodule in third_party/shaka-packager)
# ---------------------------------------------------------------------------
set(SHAKA_PACKAGER_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/shaka-packager")

if(NOT EXISTS "${SHAKA_PACKAGER_DIR}/CMakeLists.txt")
  message(FATAL_ERROR 
    "Shaka Packager submodule not found at ${SHAKA_PACKAGER_DIR}!\n"
    "Please run:\n"
    "  git submodule update --init --recursive\n"
  )
endif()

# Check and apply size_callback.patch if needed
file(READ "${SHAKA_PACKAGER_DIR}/include/packager/buffer_callback_params.h" _HEADER_CONTENT)
if(NOT _HEADER_CONTENT MATCHES "size_func")
  message(STATUS "Applying size_callback.patch to Shaka Packager...")
  find_package(Git REQUIRED)
  execute_process(
    COMMAND ${GIT_EXECUTABLE} apply "${CMAKE_CURRENT_SOURCE_DIR}/patches/size_callback.patch"
    WORKING_DIRECTORY "${SHAKA_PACKAGER_DIR}"
    RESULT_VARIABLE _PATCH_RESULT
  )
  if(NOT _PATCH_RESULT EQUAL 0)
    message(FATAL_ERROR "Failed to apply size_callback.patch to ${SHAKA_PACKAGER_DIR}")
  endif()
  message(STATUS "Patch applied successfully.")
endif()

# Shaka Packager generates protobuf headers inside its own binary dir
include_directories("${CMAKE_CURRENT_BINARY_DIR}/third_party/shaka-packager")

# Configure Shaka Packager build
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(SKIP_INTEGRATION_TESTS ON CACHE BOOL "" FORCE)

add_subdirectory(third_party/shaka-packager)

# ---------------------------------------------------------------------------
# Target: shaka_decryptor (Shared Library / DLL)
# ---------------------------------------------------------------------------
add_library(shaka_decryptor SHARED
  src/shaka_decryptor.cc
  include/shaka_decryptor.h
)

target_include_directories(shaka_decryptor
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${SHAKA_PACKAGER_DIR}
    ${SHAKA_PACKAGER_DIR}/include
    ${CMAKE_CURRENT_BINARY_DIR}/third_party/shaka-packager
)

target_link_libraries(shaka_decryptor
  PRIVATE
    absl::flags
    absl::flags_parse
    absl::log
    absl::log_globals
    absl::strings
    hex_bytes_flags
    libpackager
    license_notice
    string_utils
)

set_target_properties(shaka_decryptor PROPERTIES
  VERSION 1.0.0
  SOVERSION 1
  C_VISIBILITY_PRESET hidden
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN 1
  INTERPROCEDURAL_OPTIMIZATION TRUE
)

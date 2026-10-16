/**
 * @file units.h
 * Unit conversions used across the program, so no file needs a bare 1000 or 1000000.
 */
#pragma once
#include <cstdint>

constexpr int64_t MICROSECONDS_PER_MILLISECOND = 1000;
constexpr int64_t MICROSECONDS_PER_SECOND = 1000000;
constexpr uint32_t BYTES_PER_MEGABYTE = 1024 * 1024;

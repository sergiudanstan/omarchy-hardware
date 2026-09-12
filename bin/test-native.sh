#!/bin/bash
# Build and run the native C foundation tests without requiring CMake.

set -euo pipefail

if ! command -v gcc >/dev/null 2>&1; then
  printf 'error: gcc is required to run native tests\n' >&2
  exit 1
fi

output="$(mktemp "${TMPDIR:-/tmp}/omarchy-hardware-native.XXXXXX")"
trap 'rm -f "$output"' EXIT

gcc -Wall -Wextra -Wpedantic -Werror -std=c11 \
  -I native/include \
  -o "$output" \
  native/src/hardware_core.c native/tests/hardware_core_test.c
"$output"
printf 'native C tests passed\n'

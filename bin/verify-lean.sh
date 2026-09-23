#!/bin/bash
# Check the Lean proofs in formal/, audit their axioms, and run the differential test
# that ties the Lean models to the Python they describe.

set -euo pipefail

# The paths below are relative to the repository root.
cd "$(dirname "${BASH_SOURCE[0]}")/../formal"

if ! command -v lake >/dev/null 2>&1; then
  printf 'error: lake is required; install Lean with elan (https://lean-lang.org/install)\n' >&2
  exit 1
fi

# A proof that says "trust me" is not a proof.
if grep -rnwE 'sorry|admit|native_decide' --include='*.lean' OmarchyFormal Oracle.lean; then
  printf 'error: unfinished or compiler-trusted proof step found\n' >&2
  exit 1
fi

lake build

# Every headline theorem may rest only on Lean's three standard axioms.
axioms="$(lake env lean Axioms.lean)"
printf '%s\n' "$axioms"
if printf '%s\n' "$axioms" | grep -vE "(depends on axioms: \[(propext|Classical\.choice|Quot\.sound)(, (propext|Classical\.choice|Quot\.sound))*\]|does not depend on any axioms)$" | grep -q .; then
  printf 'error: a theorem depends on a non-standard axiom\n' >&2
  exit 1
fi

python3 tests/differential.py "$@"

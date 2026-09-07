#!/usr/bin/env bash
# Run from any directory. RTK keeps successful test output compact.
set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
if command -v rtk >/dev/null 2>&1; then
    exec rtk test python3 -m unittest discover -s tests "$@"
fi
exec python3 -m unittest discover -s tests "$@"

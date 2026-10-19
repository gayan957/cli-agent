#!/bin/bash
cat > /dev/null
command -v pytest >/dev/null 2>&1 || exit 0
output=$(pytest -q 2>&1) || { echo "$output" | tail -30 >&2; exit 2; }
exit 0

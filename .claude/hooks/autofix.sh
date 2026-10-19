#!/bin/bash
cat > /dev/null
command -v ruff >/dev/null 2>&1 || exit 0
ruff check --fix . >/dev/null 2>&1
exit 0

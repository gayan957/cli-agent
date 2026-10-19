#!/bin/bash
cat > /dev/null
echo "Git branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
exit 0

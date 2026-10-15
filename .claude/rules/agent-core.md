---
paths: va_agent/core/**
---
# Agent Core
- Read mutable state as config.NAME, never import the name directly
- Call cross-module functions through their module so tests can patch them

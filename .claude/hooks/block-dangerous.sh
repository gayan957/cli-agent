#!/bin/bash
input=$(cat)
cmd=$(printf '%s' "$input" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))")
for bad in "rm -rf" "sudo" "mkfs" "dd if=" ":(){"; do
  if [[ "$cmd" == *"$bad"* ]]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"blocked by block-dangerous.sh: %s"}}\n' "$bad"
    exit 0
  fi
done
exit 0

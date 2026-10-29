#!/usr/bin/env bash
# Stop hook: print slice progress from docs/PLAN.md after every response.
CLAUDE_PROJECT_DIR="$(pwd)"
PLAN="$CLAUDE_PROJECT_DIR/docs/PLAN.md"
[ -f "$PLAN" ] || exit 0
# Strip CR so CRLF checkouts match the patterns below.
LINES=$(tr -d '\r' < "$PLAN")
DONE=$(printf '%s\n' "$LINES" | grep -c '^- \[x\] \*\*Slice')
TOTAL=$(printf '%s\n' "$LINES" | grep -c '^- \[[ x]\] \*\*Slice')
NEXT=$(printf '%s\n' "$LINES" | grep -m1 '^- \[ \] \*\*Slice' | sed -E 's/^- \[ \] \*\*(Slice [0-9]+): ([^*]+)\*\*.*/\1 — \2/')
if [ "$DONE" -eq "$TOTAL" ]; then
  MSG="PLAN.md: all $TOTAL slices done."
else
  MSG="PLAN.md: $DONE/$TOTAL slices done · next: $NEXT"
fi
jq -n --arg msg "$MSG" '{systemMessage: $msg}'
exit 0

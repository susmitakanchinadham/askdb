#!/usr/bin/env bash
# Stop hook: print slice progress from docs/PLAN.md after every response.
PLAN=
"$CLAUDE_PROJECT_DIR/docs/PLAN.md"
[ -f "$PLAN" ] || exit 0
DONE=$(grep -c '^- \[x\] \*\*Slice' "$PLAN")
TOTAL=$(grep -c '^- \[[ x]\] \*\*Slice' "$PLAN")
NEXT=$(grep -m1 '^- \[ \] \*\*Slice' "$PLAN" | sed -E 's/^- \[ \] \*\*(Slice [0-
9]+): ([^*]+)\*\*.*/\1 — \2/')
if [ "$DONE" -eq "$TOTAL" ]; then
MSG="PLAN.md: all $TOTAL slices done."
else
MSG="PLAN.md: $DONE/$TOTAL slices done · next: $NEXT"
fi
jq -n --arg msg "$MSG" '{systemMessage: $msg}'
exit 0
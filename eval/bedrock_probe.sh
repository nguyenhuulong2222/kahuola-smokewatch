#!/usr/bin/env bash
# Probe Bedrock Converse every 30 min until it answers (account verification) or until
# the fallback decision time (12:00 HST Sun 2026-10-25 = 22:00Z). Tiny request.
MODEL=${BEDROCK_MODEL_ID:-us.anthropic.claude-sonnet-5-5}
STOP=$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "2026-10-25T22:00:00Z" +%s)
while [ "$(date -u +%s)" -lt "$STOP" ]; do
  out=$(aws bedrock-runtime converse --region us-east-1 --model-id "$MODEL" \
        --messages '[{"role":"user","content":[{"text":"Reply with OK."}]}]' \
        --inference-config maxTokens=200 --query "[stopReason,usage]" --output json 2>&1)
  echo "$(date -u +%FT%TZ) $(echo "$out" | tr '\n' ' ' | cut -c1-220)"
  if ! echo "$out" | grep -qE "AccessDenied|Exception|error"; then echo "BEDROCK_OK"; exit 0; fi
  sleep 1800
done
echo "BEDROCK_STILL_BLOCKED at deadline"; exit 3

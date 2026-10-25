#!/usr/bin/env bash
# Full eval pipeline, safe to re-run: prefetch skips cached frames, run_eval --resume
# skips finished cases. Run detached:
#   nohup eval/run_all.sh > eval/logs/run_all.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
# local secrets (e.g. FIRMS_MAP_KEY for NOAA-20 in the polar comparison); gitignored
if [ -f .env.local ]; then set -a; . ./.env.local; set +a; fi
export FIRMS_ARCHIVE_DIR=${FIRMS_ARCHIVE_DIR:-.cache/firms}
mkdir -p eval/logs
EVENTS=lahaina_pm,olinda,kula,lahaina_am,pulehu,kohala_ranch,mauna_kea_resort
NEGS=clear_0806,cloudy_0128,vog_0110,vog_0912,glint_0615
# another prefetch already running (e.g. started by hand): wait for it
while pgrep -f "eval/prefetch.py" >/dev/null; do sleep 20; done
echo "$(date) prefetch events"
$PY eval/prefetch.py --only $EVENTS --workers 10 --horizon-min 75 >> eval/logs/prefetch_events.log 2>&1
echo "$(date) prefetch negatives"
$PY eval/prefetch.py --only $NEGS --workers 10 >> eval/logs/prefetch_neg.log 2>&1
grep -h FAILED eval/logs/prefetch_*.log | tail -5
echo "$(date) eval"
$PY eval/run_eval.py --resume >> eval/logs/eval_full.log 2>&1
echo "$(date) done rc=$?"

#!/bin/sh
# update-ntpviz2.sh - Build and deploy ntpviz 2.0 dashboard
# Replaces: update-ntp-page-1d.sh and update-ntp-page-7d.sh

set -e

BASEDIR=/home/rimuru/ntpviz2
OUTDIR=$BASEDIR
LOGDIR=/var/log/ntpsec
SCRIPT=$BASEDIR/ntpviz2-build.py

# Run at low priority
renice -n 19 $$ > /dev/null 2>&1 || true

# Build JSON data for both periods
python3 "$SCRIPT" -d "$LOGDIR" -o "$OUTDIR" -p 1,7 -c

# Deploy
cd "$OUTDIR"
git add -A
git diff --cached --quiet && exit 0
git commit -m "Update $(date -u '+%Y-%m-%d %H:%M UTC')"
git push origin main -f

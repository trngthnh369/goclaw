#!/bin/sh
# deploy.sh - install this skill into the running gateway as the next skills-store version.
#
# Run on the Docker host from the repo root:   sh skills/video-factory/deploy/deploy.sh
#
# Agents resolve the skill as the highest numbered folder under
# /app/data/skills-store/video-factory/ (a named volume, so it survives restarts and
# image rebuilds). A new version is copied to a temporary folder, its tests run
# there, and only then is it renamed into place, so a broken copy is never picked up.
# Runs already in progress keep the version they started with: every command they
# were handed carries its absolute, versioned path.
set -e
CONTAINER=${GOCLAW_CONTAINER:-goclaw-goclaw-1}
tar --exclude='__pycache__' -C skills -cf - video-factory | MSYS_NO_PATHCONV=1 docker exec -i -u goclaw "$CONTAINER" sh -c '
set -e
store=/app/data/skills-store/video-factory
mkdir -p "$store"
last=$(ls "$store" | grep -E "^[0-9]+$" | sort -n | tail -1)
next=$(( ${last:-0} + 1 ))
staging="$store/.staging-$next"
rm -rf "$staging"
mkdir -p "$staging"
tar -xf - -C "$staging"
cd "$staging/video-factory"
if ! python3 -X utf8 tests/test_core.py > "$staging/tests.log" 2>&1; then
  tail -20 "$staging/tests.log"
  echo "NOT DEPLOYED: tests failed in the staged copy ($staging)"
  exit 1
fi
find . -name __pycache__ -type d -prune -exec rm -rf {} +
mv "$staging/video-factory" "$store/$next"
rm -rf "$staging"
echo "DEPLOYED video-factory version $next (tests passed); agents use it from their next run"
'

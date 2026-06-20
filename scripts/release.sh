#!/usr/bin/env bash
# Tag a release and publish it on GitHub, using the top section of CHANGELOG.md as notes.
#
# Usage:
#   ./scripts/release.sh 0.3.0          # tags v0.3.0 from the top CHANGELOG entry
#   ./scripts/release.sh v0.3.0         # 'v' prefix optional
#   ./scripts/release.sh 0.3.0 --pre    # mark as a prerelease
#
# Preconditions: clean working tree on `main`, pushed to origin, `gh` authenticated, and the
# top CHANGELOG.md entry's version matching the version you pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- args ---------------------------------------------------------------------------
RAW_VERSION="${1:-}"
PRERELEASE=""
[ "${2:-}" = "--pre" ] && PRERELEASE="--prerelease"
if [ -z "$RAW_VERSION" ]; then
  echo "ERROR: version required, e.g. ./scripts/release.sh 0.3.0" >&2
  exit 1
fi
VERSION="${RAW_VERSION#v}"          # strip a leading 'v' if present
TAG="v${VERSION}"

CHANGELOG="$ROOT/CHANGELOG.md"
[ -f "$CHANGELOG" ] || { echo "ERROR: CHANGELOG.md not found." >&2; exit 1; }

# --- preflight checks ---------------------------------------------------------------
command -v gh >/dev/null 2>&1 || { echo "ERROR: GitHub CLI 'gh' is not installed." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: 'gh' is not authenticated (run: gh auth login)." >&2; exit 1; }

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "ERROR: on branch '$BRANCH'; release from 'main'." >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is not clean. Commit or stash first." >&2
  exit 1
fi
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "ERROR: tag $TAG already exists." >&2
  exit 1
fi

# The top CHANGELOG entry must match the version being released.
TOP_HEADING="$(grep -m1 '^## \[' "$CHANGELOG" || true)"
case "$TOP_HEADING" in
  *"[$VERSION]"*) : ;;
  *)
    echo "ERROR: top CHANGELOG entry is:" >&2
    echo "    $TOP_HEADING" >&2
    echo "  but you asked to release $VERSION. Add a '## [$VERSION] — …' section first." >&2
    exit 1 ;;
esac

# --- derive title + notes from the top CHANGELOG section ----------------------------
# Title: text after the heading's version tag. Use bash parameter expansion (locale-safe;
# avoids byte-range issues with the multibyte em-dash). Heading looks like:
#   ## [0.2.0] — Analysis, weather, …
HEADING_TITLE="${TOP_HEADING#*] }"   # drop "## [ver] "  -> "— Analysis, …"
HEADING_TITLE="${HEADING_TITLE#— }"  # drop a leading em-dash + space
HEADING_TITLE="${HEADING_TITLE#- }"  # …or a plain hyphen
if [ -n "$HEADING_TITLE" ] && [ "$HEADING_TITLE" != "$TOP_HEADING" ]; then
  TITLE="$TAG — $HEADING_TITLE"
else
  TITLE="$TAG — MeterMind"
fi

# Notes: everything under the first '## [' heading up to (but excluding) the next one.
NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT
awk '
  /^## \[/ { c++; if (c==1) next }   # skip the first heading line itself
  c==1     { print }
  c==2     { exit }
' "$CHANGELOG" | sed '/^[[:space:]]*$/{:a;N;/\n[[:space:]]*$/ba;s/\n[[:space:]]*$//}' > "$NOTES_FILE"

if [ ! -s "$NOTES_FILE" ]; then
  echo "ERROR: could not extract release notes for $VERSION from CHANGELOG.md." >&2
  exit 1
fi

# --- confirm ------------------------------------------------------------------------
echo "▶ Release:  $TAG"
echo "▶ Title:    $TITLE"
echo "▶ Branch:   $BRANCH @ $(git rev-parse --short HEAD)"
[ -n "$PRERELEASE" ] && echo "▶ Prerelease: yes"
echo "▶ Notes (from CHANGELOG.md):"
sed 's/^/    /' "$NOTES_FILE" | head -20
echo "    …"
printf "Proceed? [y/N] "
read -r ANSWER
case "$ANSWER" in [yY]|[yY][eE][sS]) : ;; *) echo "Aborted."; exit 1 ;; esac

# --- push latest main, then create the release (gh tags the commit) -----------------
echo "▶ Pushing main…"
git push origin main

echo "▶ Creating GitHub release $TAG…"
gh release create "$TAG" \
  --target main \
  --title "$TITLE" \
  --notes-file "$NOTES_FILE" \
  $PRERELEASE

echo "✓ Released: $(gh release view "$TAG" --json url --jq .url)"

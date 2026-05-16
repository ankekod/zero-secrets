#!/bin/bash
# sandbox-clone — fetches a real clone of a GitHub repo (with full .git/
# history) into /workspace, brokered by the control plane. The control
# plane does the actual `git clone` server-side using the PAT, then tars
# up the working tree (including .git/) and streams it back. To push
# commits back to GitHub, use the github MCP tools.
#
# Usage:
#   sandbox-clone                       # uses $GITHUB_REPO @ $GITHUB_BRANCH
#   sandbox-clone owner/repo
#   sandbox-clone owner/repo branch-or-tag-or-sha
set -e

if [ -z "$SESSION_TOKEN" ] || [ -z "$CONTROL_PLANE_URL" ]; then
    echo "sandbox-clone: SESSION_TOKEN and CONTROL_PLANE_URL must be set" >&2
    echo "(these are normally exported by ~/.bashrc — run from an interactive shell)" >&2
    exit 1
fi

SLUG="${1:-$GITHUB_REPO}"
REF="${2:-$GITHUB_BRANCH}"

if [ -z "$SLUG" ] || [[ "$SLUG" != */* ]]; then
    echo "Usage: sandbox-clone [owner/repo] [ref]" >&2
    echo "       defaults to \$GITHUB_REPO and \$GITHUB_BRANCH" >&2
    exit 2
fi

OWNER="${SLUG%/*}"
REPO="${SLUG#*/}"
TARGET="/workspace/$REPO"

if [ -e "$TARGET" ]; then
    echo "sandbox-clone: $TARGET already exists — remove it first or pick a different repo" >&2
    exit 3
fi

URL="$CONTROL_PLANE_URL/v1/repo/tarball?owner=$OWNER&repo=$REPO"
if [ -n "$REF" ]; then
    URL="${URL}&ref=${REF}"
fi

echo "sandbox-clone: fetching $OWNER/$REPO${REF:+@$REF} via control plane..."
mkdir -p "$TARGET"

# Fetch the tarball to a temp file first so we can distinguish "control
# plane returned an error JSON body" (surfaceable diagnostic) from "tarball
# is corrupt" (surface tar's error). Once HTTP 200 is confirmed, we pipe
# the bytes into tar -xz.
TMPTAR="$(mktemp /tmp/sandbox-clone.XXXXXX.tar.gz)"
trap 'rm -f "$TMPTAR"' EXIT

HTTP_CODE=$(curl -sSL --max-time 300 -o "$TMPTAR" \
    -w "%{http_code}" \
    -H "Authorization: Bearer $SESSION_TOKEN" \
    "$URL")

if [ "$HTTP_CODE" != "200" ]; then
    echo "sandbox-clone: control plane returned HTTP $HTTP_CODE" >&2
    echo "  body:" >&2
    sed 's/^/    /' "$TMPTAR" >&2
    echo "" >&2
    rm -rf "$TARGET"
    exit 4
fi

if ! tar -xzf "$TMPTAR" -C "$TARGET"; then
    echo "sandbox-clone: tar extract failed" >&2
    rm -rf "$TARGET"
    exit 4
fi

cd "$TARGET"
echo "sandbox-clone: $TARGET ready ($(git rev-list --count HEAD) commits, branch $(git rev-parse --abbrev-ref HEAD))"
echo ""
echo "Full git history is available — log, diff, branch, checkout, merge"
echo "all work against the real upstream history."
echo ""
echo "The remote 'origin' points at github.com but cannot be fetched from"
echo "directly (no network egress). To send changes back to GitHub, use the"
echo "github MCP server: push_files / create_or_update_file / create_pull_request."

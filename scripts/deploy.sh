#!/usr/bin/env bash
# Deploy catdash to the box that runs it (a rootless podman quadlet).
#
#   scripts/deploy.sh            push main, wait for the image build, roll it out
#   scripts/deploy.sh --check    preflight only: git state, workflow status, host
#   scripts/deploy.sh --no-push  don't push; deploy the commit already on the remote
#   scripts/deploy.sh --no-wait  don't wait for CI; roll out whatever :latest is now
#
# The flow matches how the deployment is set up: GitHub Actions builds the image
# on every push to main and publishes ghcr.io/<owner>/catdash:latest (see
# .github/workflows/publish.yml); the host runs it from a quadlet unit that
# auto-updates from the registry. This script just makes that deterministic for
# the commit you're on: it waits for *this* commit's build, pulls it, verifies
# the pulled image was built from this commit, restarts the unit only when the
# image actually changed, and checks /healthz before declaring success.
#
# Overrides (env): DEPLOY_HOST (ssh target, default fworkai), DEPLOY_UNIT
# (systemd user unit / container name, default catdash), DEPLOY_IMAGE.

set -euo pipefail

HOST="${DEPLOY_HOST:-fworkai}"
UNIT="${DEPLOY_UNIT:-catdash}"
IMAGE="${DEPLOY_IMAGE:-ghcr.io/chrisrico/catdash:latest}"
WORKFLOW="publish.yml"
BRANCH="main"
# The git remote that GitHub Actions builds from (this repo names it "main").
REMOTE="$(git config --get "branch.${BRANCH}.remote" 2>/dev/null || echo origin)"

PUSH=1 WAIT=1 CHECK=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --no-push) PUSH=0 ;;
    --no-wait) WAIT=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "$@"; }

cd "$(git rev-parse --show-toplevel)"

# --- Preflight: the commit we're deploying ---------------------------------
say "Preflight"
command -v gh >/dev/null || fail "gh (GitHub CLI) is required to watch the image build"
[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || fail "deploy from $BRANCH (you're on $(git rev-parse --abbrev-ref HEAD))"
[ -z "$(git status --porcelain)" ] || fail "working tree is not clean; commit or stash first"
SHA="$(git rev-parse HEAD)"
SHORT="${SHA:0:7}"
ok "on $BRANCH at $SHORT: $(git log -1 --format=%s)"

git fetch -q "$REMOTE" "$BRANCH"
if ! git merge-base --is-ancestor "$REMOTE/$BRANCH" HEAD; then
  fail "local $BRANCH is behind $REMOTE/$BRANCH by $(git rev-list --count "HEAD..$REMOTE/$BRANCH") commit(s); pull --rebase first"
fi

if [ "$PUSH" = 1 ] && [ "$CHECK" = 0 ]; then
  if [ "$(git rev-parse "@{u}" 2>/dev/null)" = "$SHA" ]; then
    ok "already pushed to $REMOTE/$BRANCH"
  else
    say "Pushing $BRANCH to $REMOTE"
    git push "$REMOTE" "$BRANCH"
  fi
else
  [ "$(git rev-parse "$REMOTE/$BRANCH")" = "$SHA" ] || fail "HEAD is not what's on $REMOTE/$BRANCH; push first (or drop --no-push)"
  ok "$REMOTE/$BRANCH is at $SHORT"
fi

remote true 2>/dev/null || fail "can't ssh to $HOST (is Tailscale up?)"
PORT="$(remote "podman port $UNIT 8080/tcp 2>/dev/null | head -1 | sed 's/.*://'")"
ok "$HOST reachable; $UNIT publishes dashboard port ${PORT:-?}"

# --- The image build for this commit ---------------------------------------
if [ "$WAIT" = 1 ]; then
  say "Waiting for the '$WORKFLOW' build of $SHORT"
  RUN_ID=""
  for _ in $(seq 1 24); do  # the run appears a few seconds after the push
    RUN_ID="$(gh run list --workflow "$WORKFLOW" --commit "$SHA" --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)"
    [ -n "$RUN_ID" ] && break
    sleep 5
  done
  [ -n "$RUN_ID" ] || fail "no $WORKFLOW run found for $SHORT after 2 minutes"
  if [ "$CHECK" = 1 ]; then
    gh run view "$RUN_ID" --json status,conclusion,url --jq '"  run \(.url): \(.status) \(.conclusion // "")"'
  else
    gh run watch "$RUN_ID" --exit-status --interval 10 >/dev/null || fail "the image build failed: $(gh run view "$RUN_ID" --json url --jq .url)"
    ok "image built and pushed for $SHORT"
  fi
fi

if [ "$CHECK" = 1 ]; then
  say "Currently deployed on $HOST"
  remote "podman image inspect \$(podman inspect $UNIT --format '{{.Image}}') --format '  running an image built from {{index .Config.Labels \"org.opencontainers.image.revision\"}} at {{.Created}}'; systemctl --user is-active $UNIT | sed 's/^/  unit: /'"
  exit 0
fi

# --- Roll out on the host -----------------------------------------------------
say "Pulling $IMAGE on $HOST"
# Compare image IDs, not digests: a container's .ImageDigest is the manifest
# digest it was pulled by, which differs from the image's own .Digest.
BEFORE="$(remote "podman inspect $UNIT --format '{{.Image}}'")"
remote "podman pull -q $IMAGE" >/dev/null
AFTER="$(remote "podman image inspect $IMAGE --format '{{.Id}}'")"
BUILT_FROM="$(remote "podman image inspect $IMAGE --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}'")"
if [ "$WAIT" = 1 ] && [ "$BUILT_FROM" != "$SHA" ]; then
  fail "pulled image was built from ${BUILT_FROM:0:7}, not $SHORT — the registry hasn't caught up; retry in a minute"
fi
ok "pulled image built from ${BUILT_FROM:0:7} (id ${AFTER:0:12})"

if [ "$BEFORE" = "$AFTER" ]; then
  ok "$UNIT is already running this image; nothing to restart"
else
  say "Restarting $UNIT"
  remote "systemctl --user restart $UNIT"
fi

say "Waiting for /healthz"
HEALTHY=0
for _ in $(seq 1 30); do
  if remote "curl -sf -m 3 http://127.0.0.1:${PORT:-8080}/healthz" >/dev/null 2>&1; then HEALTHY=1; break; fi
  sleep 2
done
[ "$HEALTHY" = 1 ] || { remote "podman logs --tail 30 $UNIT" >&2; fail "$UNIT did not become healthy within 60s (logs above)"; }
ok "$UNIT healthy, running image $(remote "podman inspect $UNIT --format '{{.Image}}'" | cut -c1-12)"
remote "podman logs --since 2m $UNIT 2>&1 | grep -iE 'watchdog|controls|error|warn' | tail -8 | sed 's/^/  /'" || true
say "Deployed $SHORT to $HOST"

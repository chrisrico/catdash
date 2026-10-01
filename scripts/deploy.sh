#!/usr/bin/env bash
# Deploy catdash to the box that runs it (a rootless podman quadlet), building
# the image THERE from the committed tree — no registry, no GitHub Actions.
#
#   scripts/deploy.sh               build HEAD on the host and roll it out
#   scripts/deploy.sh --check       preflight only: git state, host, what's deployed
#   scripts/deploy.sh --build-only  build and tag on the host, but don't install/restart
#
# Flow: `git archive HEAD` is streamed over SSH into a scratch dir on the host,
# `podman build` turns it into localhost/catdash:<sha> (also tagged :latest,
# labelled with the commit), the quadlet unit in deploy/catdash.container is
# installed if it differs from what's on the host, the unit is restarted only
# if the running image changed, and /healthz is polled before success.
# Uncommitted changes are never deployed: commit first.
#
# Overrides (env): DEPLOY_HOST (ssh target, default fworkai), DEPLOY_UNIT
# (systemd user unit / container name, default catdash), DEPLOY_IMAGE
# (default localhost/catdash), DEPLOY_BRANCH (default main).

set -euo pipefail

HOST="${DEPLOY_HOST:-fworkai}"
UNIT="${DEPLOY_UNIT:-catdash}"
IMAGE="${DEPLOY_IMAGE:-localhost/catdash}"
BRANCH="${DEPLOY_BRANCH:-main}"
UNIT_FILE="deploy/catdash.container"
REMOTE_UNIT_DIR=".config/containers/systemd"
BUILD_DIR="/tmp/${UNIT}-build"

CHECK=0 BUILD_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --build-only) BUILD_ONLY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "$@"; }
# What commit the host's image/container was built from (its OCI revision label).
revision_of() { remote "podman image inspect $1 --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' 2>/dev/null" || true; }

cd "$(git rev-parse --show-toplevel)"

# --- Preflight ----------------------------------------------------------------
say "Preflight"
[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || fail "deploy from $BRANCH (you're on $(git rev-parse --abbrev-ref HEAD)); set DEPLOY_BRANCH to override"
[ -z "$(git status --porcelain)" ] || fail "working tree is not clean; commit or stash first (only committed files are deployed)"
[ -f "$UNIT_FILE" ] || fail "$UNIT_FILE is missing"
SHA="$(git rev-parse HEAD)"
SHORT="${SHA:0:7}"
ok "on $BRANCH at $SHORT: $(git log -1 --format=%s)"

remote true 2>/dev/null || fail "can't ssh to $HOST (is Tailscale up?)"
remote "command -v podman >/dev/null" || fail "podman is not installed on $HOST"
RUNNING_IMAGE="$(remote "podman inspect $UNIT --format '{{.Image}}' 2>/dev/null" || true)"
RUNNING_REV=""; [ -n "$RUNNING_IMAGE" ] && RUNNING_REV="$(revision_of "$RUNNING_IMAGE")"
PORT="$(remote "podman port $UNIT 8080/tcp 2>/dev/null | head -1 | sed 's/.*://'" || true)"
if [ -n "$RUNNING_REV" ]; then RUNNING_DESC="commit ${RUNNING_REV:0:7}"; else RUNNING_DESC="an unlabelled image"; fi
ok "$HOST reachable; $UNIT is $(remote "systemctl --user is-active $UNIT" || true), running $RUNNING_DESC"

if [ "$CHECK" = 1 ]; then
  if [ "$RUNNING_REV" = "$SHA" ]; then ok "the host already runs $SHORT"; else say "$SHORT is not deployed (host has ${RUNNING_REV:0:7})"; fi
  if remote "diff -q $REMOTE_UNIT_DIR/$(basename "$UNIT_FILE") -" < "$UNIT_FILE" >/dev/null 2>&1; then
    ok "quadlet unit on the host matches $UNIT_FILE"
  else
    say "quadlet unit on the host differs from $UNIT_FILE (deploy will install it)"
  fi
  exit 0
fi

# --- Build on the host ----------------------------------------------------------
say "Shipping $SHORT to $HOST and building $IMAGE:$SHORT"
git archive --format=tar HEAD | remote "rm -rf $BUILD_DIR && mkdir -p $BUILD_DIR && tar -x -C $BUILD_DIR"
# A real build log is worth seeing (npm + uv take a minute or two); quiet on
# success would hide where a slow step is.
remote "cd $BUILD_DIR && podman build \
  --label org.opencontainers.image.revision=$SHA \
  --label org.opencontainers.image.source=$(git remote get-url "$(git remote | head -1)" 2>/dev/null || echo unknown) \
  -t $IMAGE:$SHORT -t $IMAGE:latest . 2>&1 | tail -25 && rm -rf $BUILD_DIR" \
  || fail "podman build failed on $HOST"
BUILT_REV="$(revision_of "$IMAGE:latest")"
[ "$BUILT_REV" = "$SHA" ] || fail "built image is labelled ${BUILT_REV:0:7}, not $SHORT"
NEW_IMAGE="$(remote "podman image inspect $IMAGE:latest --format '{{.Id}}'")"
ok "built $IMAGE:$SHORT (id ${NEW_IMAGE:0:12})"

if [ "$BUILD_ONLY" = 1 ]; then
  say "Built only; not installed. Roll out with: scripts/deploy.sh"
  exit 0
fi

# --- Install the unit (if it changed) and restart (if the image changed) ---------
UNIT_CHANGED=0
if ! remote "diff -q $REMOTE_UNIT_DIR/$(basename "$UNIT_FILE") -" < "$UNIT_FILE" >/dev/null 2>&1; then
  say "Installing $UNIT_FILE on $HOST"
  remote "mkdir -p $REMOTE_UNIT_DIR && cat > $REMOTE_UNIT_DIR/$(basename "$UNIT_FILE") && systemctl --user daemon-reload" < "$UNIT_FILE"
  UNIT_CHANGED=1
  ok "unit installed and systemd reloaded"
fi

if [ "$UNIT_CHANGED" = 0 ] && [ "$RUNNING_IMAGE" = "$NEW_IMAGE" ]; then
  ok "$UNIT is already running this image; nothing to restart"
else
  say "Restarting $UNIT"
  remote "systemctl --user restart $UNIT"
fi

say "Waiting for /healthz"
PORT="${PORT:-$(remote "podman port $UNIT 8080/tcp 2>/dev/null | head -1 | sed 's/.*://'" || true)}"
HEALTHY=0
for _ in $(seq 1 30); do
  if remote "curl -sf -m 3 http://127.0.0.1:${PORT:-8081}/healthz" >/dev/null 2>&1; then HEALTHY=1; break; fi
  sleep 2
done
[ "$HEALTHY" = 1 ] || { remote "podman logs --tail 30 $UNIT" >&2; fail "$UNIT did not become healthy within 60s (logs above)"; }
ok "$UNIT healthy, running $(revision_of "$(remote "podman inspect $UNIT --format '{{.Image}}'")" | cut -c1-7)"
remote "podman logs --since 2m $UNIT 2>&1 | grep -iE 'watchdog|controls|error|warn' | tail -8 | sed 's/^/  /'" || true
# Old builds pile up otherwise; keep the running one and the last few tags.
remote "podman image prune -f >/dev/null 2>&1" || true
say "Deployed $SHORT to $HOST"

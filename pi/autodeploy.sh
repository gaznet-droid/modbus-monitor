#!/bin/sh
# ModPi auto-deploy. Run as root when GitHub's push webhook queues a request
# (modpi-deploy.path), and once a day at about 03:30 as a backup (modpi-deploy.timer).
#
# Pulls main from GitHub (public, read-only, no credentials on the Pi), runs the
# test suite, copies the app into /opt/modpi, restarts only what changed, checks
# the dashboard still answers, and rolls back if anything fails.
#
# A commit that fails is remembered and not retried; push a fix to move on.
# Changes to systemd units, the installers or this script are NOT applied
# automatically; the deploy stops and says so in /version and the journal.
set -eu

REPO=${MODPI_REPO:-https://github.com/gaznet-droid/modbus-monitor.git}
BRANCH=main
STATE=${MODPI_DEPLOY_STATE:-/var/lib/modpi-deploy}
SRC=$STATE/src
LIVE=${MODPI_LIVE:-/opt/modpi}
PREV=$STATE/prev
BACKUPS=$STATE/private-backups
PORT=8096

log() { echo "modpi-deploy: $*"; }

install -d -m 0755 "$STATE"
install -d -m 0700 "$BACKUPS"
exec 9>"$STATE/lock"
flock -n 9 || { log "another deploy is running"; exit 0; }

if [ ! -d "$SRC/.git" ]; then
    rm -rf "$SRC"
    git clone --quiet --depth 50 --branch "$BRANCH" "$REPO" "$SRC"
fi
git -C "$SRC" fetch --quiet --depth 50 origin "$BRANCH"
NEW=$(git -C "$SRC" rev-parse FETCH_HEAD)
OLD=$(cat "$STATE/deployed" 2>/dev/null || echo none)
[ "$NEW" != "$OLD" ] || exit 0
[ "$NEW" != "$(cat "$STATE/failed" 2>/dev/null || true)" ] || exit 0
[ "$NEW" != "$(cat "$STATE/blocked" 2>/dev/null || true)" ] || exit 0

git -C "$SRC" checkout --quiet --force "$NEW"
SUBJECT=$(git -C "$SRC" log -1 --format=%s "$NEW" | tr -d '"\\' | cut -c1-120)
log "new commit $NEW ($SUBJECT), deployed was $OLD"

write_version() {  # $1 commit, $2 note
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    printf '{"commit":"%s","deployed_at":"%s","subject":"%s"}\n' "$1" "$now" "$2" > "$LIVE/VERSION.json.tmp"
    chmod 0644 "$LIVE/VERSION.json.tmp"
    mv "$LIVE/VERSION.json.tmp" "$LIVE/VERSION.json"
}

# Units and this script are never changed automatically. If the repo's copies differ
# from what is installed on this Pi, stop and ask for a manual install.
UNITDIR=${MODPI_UNITDIR:-/etc/systemd/system}
SELF=${MODPI_SELF:-/usr/local/sbin/modpi-autodeploy}
PENDING=""
cmp -s "$SRC/pi/autodeploy.sh" "$SELF" || PENDING="autodeploy.sh"
for unit in modpi.service modpi-tuya.service modpi-tunnel.service modpi-deploy.service modpi-deploy.timer modpi-deploy.path; do
    cmp -s "$SRC/pi/$unit" "$UNITDIR/$unit" || PENDING="$PENDING $unit"
done
if [ -n "$PENDING" ]; then
    echo "$NEW" > "$STATE/blocked"
    log "BLOCKED: $NEW changes$PENDING; apply by hand (see pi/README.md, Auto-deploy)"
    write_version "$(cat "$STATE/deployed" 2>/dev/null || echo unknown)" "BLOCKED: $SUBJECT needs a manual install"
    exit 1
fi

# What actually differs between the new checkout and the live copy on this Pi.
# Comparing files (not git history) is right on the first run and after a rollback.
APP=$( {
    rsync -rlcn --delete --exclude=__pycache__ --out-format='router/%n' "$SRC/router/" "$LIVE/router/" | sed 's#^deleting #router/#'
    for f in run.py storage.py tuya_run.py; do cmp -s "$SRC/pi/$f" "$LIVE/pi/$f" || echo "pi/$f"; done
} | grep -v '/$' || true)
if [ -z "$APP" ]; then
    echo "$NEW" > "$STATE/deployed"
    write_version "$NEW" "$SUBJECT (no app changes)"
    log "no app files changed; recorded $NEW"
    exit 0
fi

# 1. Tests, as an unprivileged user, against the new checkout.
if ! (cd "$SRC" && runuser -u nobody -- env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=router:pi:tests HOME=/tmp \
        timeout 600 python3 -m unittest discover -s tests -p 'test_*.py' >"$STATE/last-test.log" 2>&1); then
    echo "$NEW" > "$STATE/failed"
    log "FAILED tests for $NEW; nothing deployed. See $STATE/last-test.log"
    write_version "$OLD" "FAILED tests: $SUBJECT"
    exit 1
fi
if ! runuser -u modpi -- python3 "$SRC/pi/run.py" --check-config >"$STATE/last-check.log" 2>&1; then
    echo "$NEW" > "$STATE/failed"
    log "FAILED config check for $NEW; nothing deployed. See $STATE/last-check.log"
    write_version "$OLD" "FAILED config check: $SUBJECT"
    exit 1
fi

# 2. Restart needed? Only Python changes need one; pages are read from disk per request.
RESTART=no; RESTART_TUYA=no
if echo "$APP" | grep -q '\.py$'; then
    RESTART=yes
    if echo "$APP" | grep -Eq '(tuya[^/]*|storage)\.py$' && systemctl is-active --quiet modpi-tuya.service; then
        RESTART_TUYA=yes
    fi
fi

# 3. Back up private state before any restart, and the live code always.
if [ "$RESTART" = yes ]; then
    tar -C /var/lib/modpi -czf "$BACKUPS/private-$(date -u +%Y%m%dT%H%M%SZ).tgz" private
    ls -1t "$BACKUPS"/private-*.tgz | tail -n +6 | xargs -r rm -f
fi
rm -rf "$PREV"
cp -a "$LIVE" "$PREV"

copy_app() {  # $1 = source root
    rsync -rlt --delete --exclude=__pycache__ "$1/router/" "$LIVE/router/"
    install -m 0644 "$1/pi/run.py" "$1/pi/storage.py" "$1/pi/tuya_run.py" "$LIVE/pi/"
    chown -R root:root "$LIVE"
    chmod -R go-w "$LIVE"
}

restart_units() {
    if [ "$RESTART" = yes ]; then systemctl restart modpi.service; fi
    if [ "$RESTART_TUYA" = yes ]; then systemctl restart modpi-tuya.service; fi
}

healthy() {  # dashboard page answers 200 and units stay up
    i=0
    while [ $i -lt 12 ]; do
        sleep 5; i=$((i + 1))
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/" || true)
        [ "$code" = 200 ] || continue
        systemctl is-active --quiet modpi.service || return 1
        if [ "$RESTART_TUYA" = yes ]; then systemctl is-active --quiet modpi-tuya.service || return 1; fi
        return 0
    done
    return 1
}

# 4. Deploy, check, roll back on failure.
copy_app "$SRC"
restart_units
if healthy; then
    echo "$NEW" > "$STATE/deployed"
    rm -f "$STATE/failed" "$STATE/blocked"
    write_version "$NEW" "$SUBJECT"
    log "deployed $NEW (restart modpi=$RESTART tuya=$RESTART_TUYA)"
else
    log "FAILED health check for $NEW; rolling back"
    rsync -a --delete "$PREV/" "$LIVE/"
    restart_units
    echo "$NEW" > "$STATE/failed"
    write_version "$OLD" "ROLLED BACK: $SUBJECT"
    exit 1
fi

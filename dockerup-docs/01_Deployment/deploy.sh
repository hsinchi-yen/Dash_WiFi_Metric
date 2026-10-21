#!/usr/bin/env bash
# Deploy the web dashboard (API + web front end) to the production host — or rehearse it on the local stack.
#
#   dockerup-docs/01_Deployment/deploy.sh --local                    # rehearsal on this machine (wifi-local)
#   dockerup-docs/01_Deployment/deploy.sh --host sit@10.20.31.40     # production (asks before every change)
#
# Steps (each prints what it does; production stops at the first failure and rolls the code back):
#   1. backup     pg_dump of the live database → DB_backups/wifitest_<ts>.dump (+ globals, SHA256)
#   2. compat     tests/db_compat.sh on that fresh dump: must print COMPATIBLE (skip with --skip-compat);
#                 full log → DB_backups/db_compat_<ts>.log
#   3. snapshot   the deployed code folder → <dir>/backups/code_<ts>.tgz (what a rollback restores)
#   4. sync       dockerup-essential/ → <dir>/dockerup-essential: top-level files copied (nothing deleted, .env kept),
#                 api/ web/ nginx/ grafana/ mirrored; docker-data, logs, uploads, the server's own extras stay
#   5. up         build the API image, (re)start api + nginx only — postgres and grafana are not touched
#   6. check      /health, /api/meta, the front end, the row count, migration log; on failure → rollback
#
# Options: --dir <remote project dir> (default /mnt/md127/WIFI_YFP_DashBoard), --skip-compat, --skip-tests, --yes
# Credentials: SSH keys, or SSHPASS in the environment (used with `sshpass -e`); nothing is ever written to a file.
# The old database stays usable by the old API (proven by db_compat), so a rollback restores code only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL=0; HOST=""; DIR="/mnt/md127/WIFI_YFP_DashBoard"; SKIP_COMPAT=0; SKIP_TESTS=0; YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --local) LOCAL=1 ;;
        --host) HOST="$2"; shift ;;
        --dir) DIR="$2"; shift ;;
        --skip-compat) SKIP_COMPAT=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        --yes) YES=1 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done
[ $LOCAL = 1 ] || [ -n "$HOST" ] || { echo "use --local or --host user@host" >&2; exit 2; }
TS=$(date +%Y%m%d_%H%M%S)
BACKUPS="$ROOT/DB_backups"
mkdir -p "$BACKUPS"
step() { echo; echo "=== $*"; }
ask() { [ $YES = 1 ] && return 0; read -r -p "$1 [y/N] " a; [ "$a" = y ] || [ "$a" = Y ]; }

if [ $LOCAL = 1 ]; then
    run() { bash -c "$*"; }
    BASE="http://127.0.0.1:8000"
    COMPOSE="cd '$ROOT/dockerup-essential' && docker compose -p wifi-local -f docker-compose.yml -f docker-compose.dev.yml"
else
    SSH=(ssh -o StrictHostKeyChecking=accept-new "$HOST")
    RSYNC_SSH="ssh -o StrictHostKeyChecking=accept-new"
    if [ -n "${SSHPASS:-}" ]; then SSH=(sshpass -e "${SSH[@]}"); RSYNC_SSH="sshpass -e $RSYNC_SSH"; fi
    run() { "${SSH[@]}" "$*"; }
    BASE="http://${HOST#*@}:8000"
    COMPOSE="cd '$DIR/dockerup-essential' && docker compose"
fi

rows() { run "docker exec wifitest-db psql -U qc -d wifitest -Atc 'SELECT COUNT(*) FROM test_record'"; }

step "0. preflight"
echo "target: $([ $LOCAL = 1 ] && echo 'local stack (wifi-local)' || echo "$HOST:$DIR")   front end: $BASE"
CHANGES=$(git -C "$ROOT" status --short)
[ -z "$CHANGES" ] || { echo "uncommitted changes (deployed as they are):"; head -5 <<<"$CHANGES"; }
echo "commit: $(git -C "$ROOT" log --oneline -1)"
if [ $SKIP_TESTS = 0 ] && [ $LOCAL = 1 ]; then
    "$ROOT/tests/run_all.sh" --quick || { echo "tests failed: not deploying"; exit 1; }
fi
BEFORE=$(rows); echo "test_record rows now: $BEFORE"
# The compose file must point the database at the data directory the running postgres uses (an .env with another
# STORAGE_PATH would one day start postgres on an empty directory). Only api + nginx are restarted, but check anyway.
PG_RUNNING=$(run "docker inspect wifitest-db --format '{{range .Mounts}}{{if eq .Destination \"/var/lib/postgresql/data\"}}{{.Source}}{{end}}{{end}}'")
PG_COMPOSE=$(run "$COMPOSE config --format json" | python3 -c 'import json,sys; v=json.load(sys.stdin)["services"]["postgres"]["volumes"]; print(next(x["source"] for x in v if x["target"]=="/var/lib/postgresql/data"))')
echo "pgdata running: $PG_RUNNING   compose: $PG_COMPOSE"
[ "$PG_RUNNING" = "$PG_COMPOSE" ] || { echo "the compose file would use another pgdata directory: fix STORAGE_PATH first"; exit 1; }

step "1. backup the live database"
run "docker exec wifitest-db pg_dump -U qc -d wifitest -Fc" > "$BACKUPS/wifitest_$TS.dump"
run "docker exec wifitest-db pg_dumpall -U qc --globals-only" > "$BACKUPS/globals_$TS.sql"
( cd "$BACKUPS" && sha256sum "wifitest_$TS.dump" "globals_$TS.sql" > "SHA256SUMS_$TS.txt" )
ls -l "$BACKUPS/wifitest_$TS.dump"
[ -s "$BACKUPS/wifitest_$TS.dump" ] || { echo "backup is empty: stop"; exit 1; }

if [ $SKIP_COMPAT = 0 ]; then
    step "2. old-database compatibility on this backup"
    # Full output kept next to the backup; the FAIL lines and the verdict are shown here.
    "$ROOT/tests/db_compat.sh" "$BACKUPS/wifitest_$TS.dump" > "$BACKUPS/db_compat_$TS.log" 2>&1 || true
    grep -E "^(===|FAIL|DIFF)|checks passed|identical|COMPATIBLE" "$BACKUPS/db_compat_$TS.log" | tail -40
    grep -q "^COMPATIBLE" "$BACKUPS/db_compat_$TS.log" \
        || { echo "db_compat did not print COMPATIBLE: not deploying (full log: $BACKUPS/db_compat_$TS.log)"; exit 1; }
fi

if [ $LOCAL = 0 ]; then
    step "3. snapshot of the deployed code"
    run "mkdir -p '$DIR/backups' && tar -C '$DIR' --exclude='dockerup-essential/docker-data' --exclude='dockerup-essential/logs' \
         --exclude='dockerup-essential/WiFiTestLogs' --exclude='dockerup-essential/build' --exclude='dockerup-essential/dist' \
         -czf '$DIR/backups/code_$TS.tgz' dockerup-essential && ls -l '$DIR/backups/code_$TS.tgz'"
    ask "Sync the new code to $HOST:$DIR and restart api + nginx?" || { echo "stopped before changing anything"; exit 0; }

    step "4. sync the code"
    # Top-level files: copied, nothing deleted (the server keeps its own extras: oldbackup/, .bak files, reports).
    rsync -az -e "$RSYNC_SSH" --exclude '*/' --exclude '.env' "$ROOT/dockerup-essential/" "$HOST:$DIR/dockerup-essential/"
    # The folders this project owns are mirrored exactly (removed modules disappear too).
    for d in api web nginx grafana; do
        rsync -az --delete -e "$RSYNC_SSH" --exclude '__pycache__/' --exclude 'log_parser.py' --exclude 'wifi_dashboard.html' \
            "$ROOT/dockerup-essential/$d/" "$HOST:$DIR/dockerup-essential/$d/"
    done
fi

rollback() {
    echo; echo "=== ROLLBACK: restoring the code of $TS"
    if [ $LOCAL = 0 ]; then
        run "cd '$DIR' && rm -rf dockerup-essential.failed && mv dockerup-essential dockerup-essential.failed && tar -xzf 'backups/code_$TS.tgz' \
             && cd dockerup-essential && docker compose up -d --build --remove-orphans --no-deps api"
        echo "previous code restored; the failed copy is $DIR/dockerup-essential.failed"
    fi
    exit 1
}

step "5. build the API image and restart api + nginx (postgres, grafana untouched)"
run "$COMPOSE build api && $COMPOSE up -d --no-deps api nginx" || rollback

step "6. checks"
ok=0
for i in $(seq 1 40); do
    if curl -sf "$BASE/health" >/dev/null; then ok=1; break; fi
    sleep 3
done
[ $ok = 1 ] || { echo "health check failed"; rollback; }
# Responses go into variables first: with pipefail, `curl | grep -q` fails whenever grep stops reading before curl
# has written the whole page (index.html is larger than a pipe buffer).
META=$(curl -sf "$BASE/api/meta") || { echo "/api/meta failed"; rollback; }
echo "${META:0:200}"
PAGE=$(curl -sf "$BASE/") || PAGE=""
grep -q "WiFi YFP" <<<"$PAGE" || { echo "front end not served"; rollback; }
curl -s -o /dev/null -w "legacy page: HTTP %{http_code} (301 to / expected: removed at deployment)\n" "$BASE/legacy/"
AFTER=$(rows); echo "test_record rows: before $BEFORE, after $AFTER"
[ "$AFTER" -ge "$BEFORE" ] || { echo "rows went down: stop"; rollback; }
run "docker logs wifitest-api --since 5m 2>&1 | grep -iE 'traceback|error' | head -5" || true
echo; echo "deployed $(git -C "$ROOT" log --oneline -1) — backup $BACKUPS/wifitest_$TS.dump"

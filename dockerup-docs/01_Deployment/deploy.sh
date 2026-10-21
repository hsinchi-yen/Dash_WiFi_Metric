#!/usr/bin/env bash
# Deploy the web dashboard (API + web front end) to the production host — or rehearse it.
#
#   dockerup-docs/01_Deployment/deploy.sh --host sit@10.20.31.40     # production (asks before switching)
#   dockerup-docs/01_Deployment/deploy.sh --replica <project dir>    # the production path against a folder on this
#                                                                    # machine (rehearsal on a copy of production)
#   dockerup-docs/01_Deployment/deploy.sh --local                    # the local test stack (wifi-local)
#
# Production keeps every deployed version side by side in <dir>/releases/ and switches between them with
# releases/switch.sh (installed from switch_release.sh; see SWITCH_RELEASE.md):
#   releases/old/           the code that ran before the first deployment (its API image: <image>:rel-old)
#   releases/new_<commit>/  each deployed commit (git archive of dockerup-essential; image wifitest-api:rel-new_<commit>)
#   releases/CURRENT        the live release
#
# Steps (each prints what it does; a failure stops the deployment and switches back to the release that was live):
#   0. preflight  row count; the compose file and the running postgres use the same pgdata
#   1. backup     on the host: <dir>/DB_backups/predeploy_<ts>/ — pg_dump, globals, schema, the code, container
#                 settings, the live API image — then copied to DB_backups/predeploy_<ts>/ here and checked (SHA256)
#   2. compat     tests/db_compat.sh on that dump: must print COMPATIBLE (skip with --skip-compat)
#   3. release    first time: the live code becomes releases/old; then this commit → releases/new_<commit>, its API
#                 image is built here and loaded on the host; switch.sh, the rollback SQL and the guide are installed
#   4. switch     releases/switch.sh new_<commit>: api + nginx recreated, postgres and grafana untouched
#   5. check      /health, /api/meta, the front end, the row count; on failure → switch.sh <previous release>
#
# Options: --dir <remote project dir> (default /mnt/md127/WIFI_YFP_DashBoard), --skip-compat, --skip-tests, --yes
# Credentials: SSH keys, or SSHPASS in the environment (used with `sshpass -e`); nothing is ever written to a file.
# Only committed code is deployed (HEAD); uncommitted changes are listed and left out.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/dockerup-docs/01_Deployment"
MODE=""; HOST=""; DIR="/mnt/md127/WIFI_YFP_DashBoard"; SKIP_COMPAT=0; SKIP_TESTS=0; YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --local) MODE=local ;;
        --host) MODE=remote; HOST="$2"; shift ;;
        --replica) MODE=replica; DIR="$(cd "$2" && pwd)"; shift ;;
        --dir) DIR="$2"; shift ;;
        --skip-compat) SKIP_COMPAT=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        --yes) YES=1 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done
[ -n "$MODE" ] || { echo "use --host user@host, --replica <dir> or --local" >&2; exit 2; }
TS=$(date +%Y%m%d_%H%M%S)
BACKUPS="$ROOT/DB_backups"
mkdir -p "$BACKUPS"
step() { echo; echo "=== $*"; }
ask() { [ $YES = 1 ] && return 0; read -r -p "$1 [y/N] " a; [ "$a" = y ] || [ "$a" = Y ]; }

case $MODE in
    local)
        run() { bash -c "$*"; }
        BASE="http://127.0.0.1:8000"
        COMPOSE="cd '$ROOT/dockerup-essential' && docker compose -p wifi-local -f docker-compose.yml -f docker-compose.dev.yml" ;;
    replica)
        run() { bash -c "$*"; }
        DEST=""; RS=(rsync -a)
        BASE="http://127.0.0.1:8000"
        COMPOSE="cd '$DIR/dockerup-essential' && docker compose" ;;
    remote)
        SSH=(ssh -o StrictHostKeyChecking=accept-new "$HOST")
        RSYNC_SSH="ssh -o StrictHostKeyChecking=accept-new"
        if [ -n "${SSHPASS:-}" ]; then SSH=(sshpass -e "${SSH[@]}"); RSYNC_SSH="sshpass -e $RSYNC_SSH"; fi
        run() { "${SSH[@]}" "$*"; }
        DEST="$HOST:"; RS=(rsync -az -e "$RSYNC_SSH")
        BASE="http://${HOST#*@}:8000"
        COMPOSE="cd '$DIR/dockerup-essential' && docker compose" ;;
esac
REL="$DIR/releases"

rows() { run "docker exec wifitest-db psql -U qc -d wifitest -Atc 'SELECT COUNT(*) FROM test_record'"; }

step "0. preflight"
echo "target: $([ $MODE = local ] && echo 'local stack (wifi-local)' || echo "${HOST:-this machine}:$DIR")   front end: $BASE"
CHANGES=$(git -C "$ROOT" status --short)
[ -z "$CHANGES" ] || { echo "uncommitted changes ($([ $MODE = local ] && echo 'deployed as they are' || echo 'NOT deployed: only HEAD is')):"; head -5 <<<"$CHANGES"; }
SHA=$(git -C "$ROOT" rev-parse --short=7 HEAD)
echo "commit: $(git -C "$ROOT" log --oneline -1)"
if [ $SKIP_TESTS = 0 ] && [ $MODE = local ]; then
    "$ROOT/tests/run_all.sh" --quick || { echo "tests failed: not deploying"; exit 1; }
fi
BEFORE=$(rows); echo "test_record rows now: $BEFORE"
# The compose file must point the database at the data directory the running postgres uses (an .env with another
# STORAGE_PATH would one day start postgres on an empty directory). Only api + nginx are restarted, but check anyway.
PG_RUNNING=$(run "docker inspect wifitest-db --format '{{range .Mounts}}{{if eq .Destination \"/var/lib/postgresql/data\"}}{{.Source}}{{end}}{{end}}'")
PG_COMPOSE=$(run "$COMPOSE config --format json" | python3 -c 'import json,sys; v=json.load(sys.stdin)["services"]["postgres"]["volumes"]; print(next(x["source"] for x in v if x["target"]=="/var/lib/postgresql/data"))')
echo "pgdata running: $PG_RUNNING   compose: $PG_COMPOSE"
[ "$PG_RUNNING" = "$PG_COMPOSE" ] || { echo "the compose file would use another pgdata directory: fix STORAGE_PATH first"; exit 1; }
if [ $MODE != local ]; then
    PREV=$(run "cat '$REL/CURRENT' 2>/dev/null || true")
    echo "live release: ${PREV:-none yet (the live code becomes releases/old)}"
fi

step "1. backup"
if [ $MODE = local ]; then
    DUMP="$BACKUPS/wifitest_$TS.dump"
    run "docker exec wifitest-db pg_dump -U qc -d wifitest -Fc" > "$DUMP"
    run "docker exec wifitest-db pg_dumpall -U qc --globals-only" > "$BACKUPS/globals_$TS.sql"
    ( cd "$BACKUPS" && sha256sum "wifitest_$TS.dump" "globals_$TS.sql" > "SHA256SUMS_$TS.txt" )
else
    # On the host first (a restore there needs nothing from this machine), then a verified copy here.
    B="$DIR/DB_backups/predeploy_$TS"
    run "set -e; mkdir -p '$B'; cd '$B'
         docker exec wifitest-db pg_dump -U qc -d wifitest -Fc > wifitest.dump
         docker exec wifitest-db pg_dumpall -U qc --globals-only > globals.sql
         docker exec wifitest-db pg_dump -U qc -d wifitest --schema-only > schema_only.sql
         tar -C '$DIR' -czf code_dockerup-essential.tgz --exclude=dockerup-essential/docker-data dockerup-essential
         docker inspect \$(docker ps -aq --filter label=com.docker.compose.project=dockerup-essential) > containers.json
         (cd '$DIR/dockerup-essential' && docker compose config) > compose_config.yml
         IMG=\$(docker inspect wifitest-api --format '{{.Image}}'); echo \$IMG > api_image_id.txt
         docker save \$IMG \$(docker image inspect \$IMG --format '{{join .RepoTags \" \"}}') | gzip -1 > api_image.tar.gz
         sha256sum * > SHA256SUMS.txt; du -sh '$B'"
    "${RS[@]}" "$DEST$B" "$BACKUPS/"
    ( cd "$BACKUPS/predeploy_$TS" && sha256sum -c --quiet SHA256SUMS.txt ) || { echo "the copied backup does not match: stop"; exit 1; }
    DUMP="$BACKUPS/predeploy_$TS/wifitest.dump"
    echo "copied and checked: $BACKUPS/predeploy_$TS"
fi
ls -l "$DUMP"
[ -s "$DUMP" ] || { echo "backup is empty: stop"; exit 1; }

if [ $SKIP_COMPAT = 0 ]; then
    step "2. old-database compatibility on this backup"
    # Full output kept next to the backup; the FAIL lines and the verdict are shown here.
    "$ROOT/tests/db_compat.sh" "$DUMP" > "$BACKUPS/db_compat_$TS.log" 2>&1 || true
    grep -E "^(===|FAIL|DIFF)|checks passed|identical|COMPATIBLE" "$BACKUPS/db_compat_$TS.log" | tail -40
    grep -q "^COMPATIBLE" "$BACKUPS/db_compat_$TS.log" \
        || { echo "db_compat did not print COMPATIBLE: not deploying (full log: $BACKUPS/db_compat_$TS.log)"; exit 1; }
fi

if [ $MODE = local ]; then
    step "4. build the API image and restart api + nginx (postgres, grafana untouched)"
    run "$COMPOSE build api && $COMPOSE up -d --no-deps api nginx" || { echo "restart failed"; exit 1; }
    rollback() { exit 1; }
else
    step "3. releases"
    if [ -z "$PREV" ]; then
        # First deployment: what runs now becomes releases/old — its code and the image of the running api.
        run "set -e; mkdir -p '$REL/old'
             rsync -a --delete --exclude '/docker-data/' --exclude '/logs/' --exclude '/WiFiTestLogs/' --exclude '/build/' \
                 --exclude '/dist/' --exclude '/.env' --exclude '__pycache__/' '$DIR/dockerup-essential/' '$REL/old/'
             printf 'old — the dashboard that ran before the first deployment (snapshot $TS)\n' > '$REL/old/RELEASE.txt'
             IMG=\$(docker inspect wifitest-api --format '{{.Image}}')
             REPO=\$(cd '$DIR/dockerup-essential' && docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"services\"][\"api\"].get(\"image\") or \"dockerup-essential-api\")')
             docker tag \$IMG \${REPO%%:*}:rel-old
             echo old > '$REL/CURRENT'
             echo \"releases/old: \$(du -sh '$REL/old' | cut -f1), image \${REPO%%:*}:rel-old = \$IMG\""
        PREV=old
    fi
    NEW="new_$SHA"
    TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
    git -C "$ROOT" archive --format=tar HEAD dockerup-essential | tar -x -C "$TMP"
    printf '%s — %s (deployed %s)\n' "$NEW" "$(git -C "$ROOT" log --oneline -1)" "$TS" > "$TMP/dockerup-essential/RELEASE.txt"
    IMG=$(docker compose -p dockerup-essential -f "$TMP/dockerup-essential/docker-compose.yml" config --format json \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["api"]["image"])')
    echo "build ${IMG%:*}:rel-$NEW from $SHA here"
    docker build -q -f "$TMP/dockerup-essential/api/Dockerfile" -t "${IMG%:*}:rel-$NEW" "$TMP/dockerup-essential"
    run "mkdir -p '$REL/$NEW'"
    "${RS[@]}" --delete "$TMP/dockerup-essential/" "$DEST$REL/$NEW/"
    "${RS[@]}" "$HERE/switch_release.sh" "$DEST$REL/switch.sh"
    "${RS[@]}" "$HERE/SWITCH_RELEASE.md" "$DEST$REL/README.md"
    "${RS[@]}" "$ROOT/dockerup-docs/02_Maintenance/scripts/rollback_web_additions.sql" "$DEST$REL/"
    if [ $MODE = remote ]; then
        echo "load the image on $HOST"
        docker save "${IMG%:*}:rel-$NEW" | gzip -1 | "${SSH[@]}" 'gunzip | docker load'
    fi
    run "chmod +x '$REL/switch.sh' && '$REL/switch.sh' check '$NEW'"
    ask "Switch $HOST:$DIR from $PREV to $NEW (api + nginx restart, about a minute)?" || { echo "stopped before switching"; exit 0; }

    step "4. switch to $NEW"
    # switch.sh switches back to $PREV by itself when the new release does not come up.
    run "'$REL/switch.sh' '$NEW'" || { echo "the switch failed; $PREV is live (releases/switch.log)"; exit 1; }
    rollback() { echo; echo "=== ROLLBACK: back to $PREV"; run "'$REL/switch.sh' '$PREV'"; exit 1; }
fi

step "5. checks"
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
[ $MODE = local ] || run "'$REL/switch.sh' status"
echo; echo "deployed $(git -C "$ROOT" log --oneline -1) — backup $DUMP"
[ $MODE = local ] || echo "back to the old dashboard: $REL/switch.sh old   (forward again: $REL/switch.sh new)"

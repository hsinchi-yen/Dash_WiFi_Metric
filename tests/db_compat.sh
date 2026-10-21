#!/usr/bin/env bash
# Old-database compatibility run on a scratch PostgreSQL container (never touches wifitest-db).
#
#   tests/db_compat.sh DB_BACKUPS/wifitest_<ts>.dump [OLD_DEPLOYMENT_DIR]
#
# OLD_DEPLOYMENT_DIR is the deployed dockerup-essential/ of the old system (default: the newest
# _remote_snapshot/*/dockerup-essential). Steps:
#   1. restore the backup twice: "old" (untouched) and "prodlike"
#   2. build the API image (tag wifitest-api:compat-check) and start it on "prodlike" with no source
#      mounts — exactly as in production; its startup migration upgrades the database
#   3. tests/db_compat.py db: schema / data / Grafana / desktop-uploader checks (migrated mode)
#   4. new API on the upgraded copy: tests/golden.py check (the 40 pre-refactor responses)
#   5. the OLD API on both copies: 40 read endpoints identical; ingest / DB Tweak writes identical
#   6. rollback_web_additions.sql on a copy: schema identical to the old one, old API identical
#   7. upgrade the rolled-back copy again (redeploy after a rollback)
# Exit code 0 only when every step passes. Scratch containers are removed at the end.
set -u
DUMP=${1:?usage: tests/db_compat.sh <backup.dump> [old dockerup-essential dir]}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OLD=${2:-$(ls -d "$ROOT"/_remote_snapshot/*/dockerup-essential 2>/dev/null | tail -1)}
PORT=${COMPAT_DB_PORT:-55433}
IMAGE=wifitest-api:compat-check
WORK=$(mktemp -d)
DB=wifi-compat-db
DSN="postgresql://qc:qcpass@127.0.0.1:$PORT"
FAILED=0
step() { echo; echo "=== $*"; }
run() {     # run a check; show its FAIL lines and summary line; remember a failure
    "$@" > "$WORK/out.txt" 2>&1; local rc=$?
    grep -E "^FAIL|DIFF|Traceback|Error" "$WORK/out.txt" | head -20; tail -1 "$WORK/out.txt"
    [ $rc = 0 ] || FAILED=1
}
api_up() {   # name port database image [mounts…]
    local name=$1 port=$2 db=$3 image=$4; shift 4
    docker rm -f "$name" >/dev/null 2>&1
    docker run -d --name "$name" --network host "$@" -e DATABASE_URL="$DSN/$db" -e WIFI_DISABLE_SCHEDULER=1 \
        -e TWEAK_USER=qc -e TWEAK_PASS=qcpass -e UPLOAD_TMP_DIR=/tmp/up "$image" \
        uvicorn app:app --host 127.0.0.1 --port "$port" >/dev/null
    for _ in $(seq 1 60); do curl -sf -o /dev/null "http://127.0.0.1:$port/health" && return 0; sleep 0.5; done
    echo "API $name did not start:"; docker logs "$name" 2>&1 | tail -5; FAILED=1; return 1
}
old_api() { api_up "$1" "$2" "$3" wifitest-api:compat-check -v "$WORK/oldapp:/app:ro" -v "$WORK/samples:/samples:ro" -w /app; }
compat() { run docker run --rm --network host -v "$ROOT/tests:/tests:ro" -v "$OLD:/old:ro" -v "$WORK:/work" \
    "$IMAGE" python3 /tests/db_compat.py db "$DSN/old" "$DSN/$1" --mode "$2" \
    --grafana /old/grafana/dashboards/wifi-qc-overview.json --parser-dir /old --sample "/work/samples/$SAMPLE_REL"; }
cleanup() {
    docker rm -f -v $DB wifi-compat-new wifi-compat-old-a wifi-compat-old-b >/dev/null 2>&1
    rm -rf "$WORK"
}
trap cleanup EXIT

[ -f "$DUMP" ] && [ -d "$OLD/api" ] || { echo "need the backup file and the old deployment directory"; exit 2; }
# Sample log for the writer / ingest checks: the synthetic log that is not in production data.
SAMPLE_SRC=$ROOT/dockerup-essential/logs/20260203_182710_513126039999_001F7B6C11E4_001F7B6C11E5_PASS.txt
SAMPLE_REL=TN-98_COMPAT-CHECK/9999-261021901/$(basename "$SAMPLE_SRC")
mkdir -p "$WORK/samples/$(dirname "$SAMPLE_REL")" "$WORK/oldapp"
cp "$SAMPLE_SRC" "$WORK/samples/$SAMPLE_REL"
cp "$OLD"/api/*.py "$OLD/log_parser.py" "$OLD/wifi_dashboard.html" "$WORK/oldapp/"

step "1. scratch database, restore $(basename "$DUMP") twice"
docker rm -f -v $DB >/dev/null 2>&1
docker run -d --name $DB -p 127.0.0.1:$PORT:5432 -e POSTGRES_USER=qc -e POSTGRES_PASSWORD=qcpass \
    -v "$(cd "$(dirname "$DUMP")" && pwd):/backups:ro" postgres:16-alpine >/dev/null
for _ in $(seq 1 60); do docker exec $DB pg_isready -U qc -q && break; sleep 1; done; sleep 2
for d in old prodlike; do
    docker exec $DB createdb -U qc $d && docker exec $DB pg_restore -U qc -d $d --no-owner -j 4 "/backups/$(basename "$DUMP")" || FAILED=1
done

step "2. build $IMAGE and start it on the restored copy (no source mounts)"
docker build -q -f "$ROOT/dockerup-essential/api/Dockerfile" -t $IMAGE "$ROOT/dockerup-essential" >/dev/null || exit 1
api_up wifi-compat-new 18105 prodlike $IMAGE && docker logs wifi-compat-new 2>&1 | grep -E "wifi.migrations" | sed 's/^/    /'

step "3. schema / data / Grafana / desktop uploader (upgraded copy vs old)"
compat prodlike migrated

step "4. new API on the upgraded copy vs the pre-refactor responses"
run python3 "$ROOT/tests/golden.py" check http://127.0.0.1:18105
docker rm -f wifi-compat-new >/dev/null

step "5. old API on both copies"
old_api wifi-compat-old-a 18101 old && old_api wifi-compat-old-b 18102 prodlike
run env GOLDEN_DIR="$WORK/golden" python3 "$ROOT/tests/golden.py" capture http://127.0.0.1:18101
run env GOLDEN_DIR="$WORK/golden" python3 "$ROOT/tests/golden.py" check http://127.0.0.1:18102
run python3 "$ROOT/tests/db_compat.py" oldapi http://127.0.0.1:18101 http://127.0.0.1:18102 \
    --sample-dir /samples --work-order 9999-261021901 --expect 1
docker rm -f wifi-compat-old-a wifi-compat-old-b >/dev/null

step "6. rollback_web_additions.sql on a copy"
docker exec $DB createdb -U qc -T prodlike rolledback
docker exec -i $DB psql -q -U qc -d rolledback -v ON_ERROR_STOP=1 \
    < "$ROOT/dockerup-docs/02_Maintenance/scripts/rollback_web_additions.sql" || FAILED=1
compat rolledback rollback
old_api wifi-compat-old-b 18102 rolledback
run env GOLDEN_DIR="$WORK/golden" python3 "$ROOT/tests/golden.py" check http://127.0.0.1:18102
docker rm -f wifi-compat-old-b >/dev/null

step "7. upgrade the rolled-back copy again"
api_up wifi-compat-new 18105 rolledback $IMAGE
run python3 "$ROOT/tests/golden.py" check http://127.0.0.1:18105
compat rolledback migrated

echo
if [ $FAILED = 0 ]; then echo "COMPATIBLE: every step passed"; else echo "NOT COMPATIBLE: see FAIL lines above"; fi
exit $FAILED

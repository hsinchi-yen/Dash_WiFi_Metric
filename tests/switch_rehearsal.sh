#!/usr/bin/env bash
# Rehearse releases/switch.sh on a copy of production on this machine (after deploy.sh --replica <dir>):
# old ↔ new both ways, a write while the old dashboard is live, a failed switch that must revert by itself, a release
# without its image, the lock, --drop-web-tables and the documented full database restore.
#
#   tests/switch_rehearsal.sh <replica project dir> <predeploy dump>
#
# The replica runs on the production ports (8000, 5432, 3000): stop the local stack first. It changes the replica's
# database (one row written and restored away); nothing else.
set -uo pipefail
cd "$(dirname "$0")/.."
DIR="$(cd "$1" && pwd)"; DUMP="$2"
REL="$DIR/releases"; SW="$REL/switch.sh"
BASE=http://127.0.0.1:8000
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "ok    $*"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL  $*"; }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
psql_() { docker exec wifitest-db psql -U qc -d wifitest -Atc "$1"; }
title() { curl -s "$BASE/" | grep -o '<title>[^<]*' | head -1; }
schema() {   # the schema without comments / settings, for comparing with the production dump's
    grep -vE '^(--|\\(un)?restrict|SET |SELECT pg_catalog)' | sed '/^$/d'
}
timed() {   # timed <label> <command…>: runs it and prints how long the switch took
    local label="$1" t0=$SECONDS rc; shift
    "$@"; rc=$?; echo "      ($label: $((SECONDS - t0)) s, exit $rc)"; return $rc
}
OLD_TITLE="TN-WiFi Metrics"; NEW_TITLE="WiFi YFP"
NEW=$(cat "$REL/CURRENT")
ROWS=$(psql_ "SELECT COUNT(*) FROM test_record")
docker exec -i wifitest-db pg_restore -f - --schema-only < "$DUMP" | schema > /tmp/switch_rehearsal_prod_schema.$$
trap 'rm -f /tmp/switch_rehearsal_*.$$' EXIT

echo "== start: live $NEW, $ROWS rows"
[[ "$NEW" == new_* ]] && ok "deploy.sh left a new release live ($NEW)" || bad "live release is $NEW"
[[ "$(title)" == *"$NEW_TITLE"* ]] && ok "new dashboard served" || bad "front page: $(title)"
check "switch.sh status" "$SW" status
check "switch.sh check old" "$SW" check old
"$SW" check new_nothing >/dev/null 2>&1 && bad "check of a missing release passed" || ok "check of a missing release fails"

echo "== 1. new → old"
timed "switch old" "$SW" old && ok "switch.sh old" || bad "switch.sh old"
[ "$(cat "$REL/CURRENT")" = old ] && ok "CURRENT = old" || bad "CURRENT = $(cat "$REL/CURRENT")"
[[ "$(title)" == *"$OLD_TITLE"* ]] && ok "old dashboard served on :8000" || bad "front page: $(title)"
docker ps -a --format '{{.Names}}' | grep -qx wifitest-nginx && bad "nginx container still there" || ok "nginx container removed"
[ "$(docker inspect wifitest-api --format '{{index .Config.Labels "com.docker.compose.service"}} {{.Config.Image}}')" = "api dockerup-essential-api" ] \
    && ok "api runs the old image" || bad "api image: $(docker inspect wifitest-api --format '{{.Config.Image}}')"
check "golden 40/40 on the old dashboard" python3 tests/golden.py check "$BASE"
check "Grafana answers" curl -sf http://127.0.0.1:3000/api/health
[ -f "$DIR/dockerup-essential/web/index.html" ] && bad "web/ still in the live folder" || ok "live folder is the old code (no web/)"
[ -f "$DIR/dockerup-essential/.env" ] && ok ".env kept" || bad ".env removed"

echo "== 2. a desktop-style write while the old dashboard is live"
MAXID=$(psql_ "SELECT MAX(id) FROM test_record")
docker exec wifitest-api python log_parser.py /app/logs --dsn postgresql://qc:qcpass@postgres/wifitest 2>&1 | tail -1
R1=$(psql_ "SELECT COUNT(*) FROM test_record")
[ "$R1" -gt "$ROWS" ] && ok "old parser wrote $((R1 - ROWS)) row(s)" || bad "rows $ROWS → $R1"

echo "== 3. old → new"
timed "switch new" "$SW" new && ok "switch.sh new" || bad "switch.sh new"
[ "$(cat "$REL/CURRENT")" = "$NEW" ] && ok "CURRENT = $NEW" || bad "CURRENT = $(cat "$REL/CURRENT")"
[[ "$(title)" == *"$NEW_TITLE"* ]] && ok "new dashboard served" || bad "front page: $(title)"
check "/api/meta" curl -sf "$BASE/api/meta"
check "golden 40/40 on the new dashboard" python3 tests/golden.py check "$BASE"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/legacy/")" = 301 ] && ok "/legacy/ → 301" || bad "/legacy/ not 301"
[ "$(psql_ "SELECT COUNT(*) FROM test_record")" = "$R1" ] && ok "the row written under the old dashboard is there" || bad "row count changed"
psql_ "DELETE FROM test_record WHERE id > $MAXID" >/dev/null   # as the old DB Tweak would; golden needs the original rows
[ "$(psql_ "SELECT COUNT(*) FROM test_record")" = "$ROWS" ] && ok "rehearsal row removed again" || bad "rows not back to $ROWS"

echo "== 4. a release that does not come up must switch back by itself"
rm -rf "$REL/new_broken"; cp -a "$REL/$NEW" "$REL/new_broken"
echo "this is not nginx syntax" > "$REL/new_broken/nginx/default.conf"
docker tag "wifitest-api:rel-$NEW" wifitest-api:rel-new_broken
timed "switch new_broken" "$SW" new_broken && bad "broken release reported success" || ok "switch to the broken release fails"
[ "$(cat "$REL/CURRENT")" = "$NEW" ] && ok "CURRENT back to $NEW" || bad "CURRENT = $(cat "$REL/CURRENT")"
[[ "$(title)" == *"$NEW_TITLE"* ]] && ok "new dashboard served again" || bad "front page: $(title)"
check "/health after the revert" curl -sf "$BASE/health"
[ "$(cat "$REL/LAST_NEW")" = "$NEW" ] && ok "'new' still means $NEW" || bad "LAST_NEW = $(cat "$REL/LAST_NEW")"
grep -q "this is not nginx" "$DIR/dockerup-essential/nginx/default.conf" && bad "broken nginx conf left live" || ok "live code is $NEW again"
rm -rf "$REL/new_broken"; docker rmi -f wifitest-api:rel-new_broken >/dev/null

echo "== 5. a release without its image is refused before anything changes"
cp -a "$REL/$NEW" "$REL/new_noimage"
API_ID=$(docker inspect wifitest-api --format '{{.Id}}')
"$SW" new_noimage >/dev/null 2>&1 && bad "release without image switched" || ok "release without image refused"
[ "$(docker inspect wifitest-api --format '{{.Id}}')" = "$API_ID" ] && ok "api container untouched" || bad "api container recreated"
rm -rf "$REL/new_noimage"

echo "== 6. one switch at a time"
flock "$REL/.lock" sleep 6 & sleep 1
"$SW" status >/dev/null 2>&1 && bad "second switch.sh ran during a switch" || ok "second switch.sh refused while one runs"
wait

echo "== 7. old --drop-web-tables: the database structure of before the deployment"
timed "switch old --drop-web-tables" "$SW" old --drop-web-tables && ok "switch.sh old --drop-web-tables" || bad "switch.sh old --drop-web-tables"
docker exec wifitest-db pg_dump -U qc -d wifitest --schema-only | schema > /tmp/switch_rehearsal_now.$$
diff -q /tmp/switch_rehearsal_prod_schema.$$ /tmp/switch_rehearsal_now.$$ >/dev/null \
    && ok "schema identical to the pre-deployment production dump" || bad "schema differs: $(diff /tmp/switch_rehearsal_prod_schema.$$ /tmp/switch_rehearsal_now.$$ | head -5)"
[ "$(psql_ "SELECT COUNT(*) FROM test_record")" = "$ROWS" ] && ok "every test_record row kept" || bad "row count changed"
ls "$DIR"/DB_backups/before_drop_web_tables_*.dump >/dev/null 2>&1 && ok "dump written before dropping" || bad "no dump before dropping"
check "golden 40/40 on the old dashboard, old schema" python3 tests/golden.py check "$BASE"

echo "== 8. new again on the rolled-back database (tables recreated)"
timed "switch new" "$SW" new && ok "switch.sh new" || bad "switch.sh new"
check "/api/meta" curl -sf "$BASE/api/meta"
[ "$(psql_ "SELECT COUNT(*) FROM information_schema.tables WHERE table_name IN ('app_meta','fail_analysis','app_users')")" = 3 ] \
    && ok "the new dashboard's tables are back" || bad "tables not recreated"
check "golden 40/40" python3 tests/golden.py check "$BASE"

echo "== 9. restore-db: the whole database back to the pre-deployment backup (SWITCH_RELEASE.md)"
docker exec wifitest-api python log_parser.py /app/logs --dsn postgresql://qc:qcpass@postgres/wifitest >/dev/null 2>&1
[ "$(psql_ "SELECT COUNT(*) FROM test_record")" -gt "$ROWS" ] && ok "a row written after the backup (to be lost)" || bad "no row written"
echo no | "$SW" restore-db "$DUMP" >/dev/null 2>&1 && bad "restore-db ran without yes" || ok "restore-db without yes: refused"
"$SW" restore-db /etc/hostname --yes >/dev/null 2>&1 && bad "restore-db took a non-dump" || ok "restore-db of a non-dump: refused"
[ "$(cat "$REL/CURRENT")" = "$NEW" ] && ok "refusals changed nothing (still $NEW)" || bad "CURRENT = $(cat "$REL/CURRENT")"
timed "restore-db" "$SW" restore-db "$DUMP" --yes && ok "switch.sh restore-db" || bad "switch.sh restore-db"
[ "$(cat "$REL/CURRENT")" = old ] && ok "old dashboard live after restore-db" || bad "CURRENT = $(cat "$REL/CURRENT")"
[ "$(psql_ "SELECT COUNT(*) FROM test_record")" = "$ROWS" ] && ok "rows back to $ROWS (the later row is gone, as documented)" || bad "rows: $(psql_ "SELECT COUNT(*) FROM test_record")"
docker exec wifitest-db pg_dump -U qc -d wifitest --schema-only | schema > /tmp/switch_rehearsal_now.$$
diff -q /tmp/switch_rehearsal_prod_schema.$$ /tmp/switch_rehearsal_now.$$ >/dev/null && ok "schema identical to the backup" || bad "schema differs"
ls "$DIR"/DB_backups/before_restore_*.dump >/dev/null 2>&1 && ok "safety dump written before restoring" || bad "no safety dump"
check "golden 40/40 on the restored database" python3 tests/golden.py check "$BASE"

echo "== 10. back to new for the end"
"$SW" new >/dev/null 2>&1 && ok "switch.sh new" || bad "switch.sh new"
check "golden 40/40" python3 tests/golden.py check "$BASE"
"$SW" status

echo; echo "$PASS/$((PASS + FAIL)) checks passed"
[ $FAIL = 0 ]

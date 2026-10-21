#!/usr/bin/env bash
# Switch the WiFi YFP dashboard between deployed releases: the old single-page dashboard and the new web dashboard.
# Runs on the production host itself, installed by deploy.sh as <project>/releases/switch.sh; needs only docker
# (the sit user is in the docker group). About 10 seconds; PostgreSQL and Grafana keep running throughout.
#
#   releases/switch.sh status                       which release is live, what can be switched to
#   releases/switch.sh old                          back to the dashboard that ran before the deployment
#   releases/switch.sh new                          the new release that was live last (the deployed one)
#   releases/switch.sh <release>                    any folder under releases/
#   releases/switch.sh check <release>              only check that the switch can be made (changes nothing)
#   releases/switch.sh old --drop-web-tables        also return the database structure to the pre-deployment one
#   releases/switch.sh restore-db <dump> [--yes]    the whole database back to a backup (rows written since are LOST)
#
# A switch:
#   1. checks the release folder, its compose file and its API image (kept per release as <image>:rel-<release>)
#   2. mirrors the release's code into dockerup-essential/; docker-data, logs, WiFiTestLogs, build, dist, grafana and
#      .env there are never touched
#   3. recreates api (and nginx for the new dashboard), removes the containers the release does not have
#   4. waits for /health and the front page; when they fail, it switches back to the release that was live before
#
# The database is not changed by a switch: the old dashboard runs on the upgraded database as it is (tests/db_compat.sh
# proves it). --drop-web-tables (old only) first writes a pg_dump to DB_backups/, then runs rollback_web_additions.sql:
# the new dashboard's own tables (accounts, upload log, plans, notes, reports, FAIL classification, LLM usage) are
# dropped; every test_record row stays. The new dashboard recreates the tables, empty, on its next start.
# restore-db (only when the database itself is damaged) switches to old, writes a pg_dump of the database as it is, runs
# rollback_web_additions.sql (the new dashboard's objects depend on test_record and are not in a pre-deployment
# backup), then restores the backup in one transaction: on any error nothing of it is applied.
set -euo pipefail
shopt -s inherit_errexit
REL="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="${WIFI_DIR:-$(dirname "$REL")}"
LIVE="$ROOT/dockerup-essential"
PROJECT=dockerup-essential
PORT="${WIFI_HTTP_PORT:-8000}"
TS=$(date +%Y%m%d_%H%M%S)

log() { echo "$*" >&2; echo "$(date '+%F %T') $*" >> "$REL/switch.log"; }   # stderr: functions return values on stdout
die() { log "ERROR: $*"; exit 1; }
current() { cat "$REL/CURRENT" 2>/dev/null || echo unknown; }

resolve() {   # resolve <name>: old / new / a folder name → the release folder name
    case "$1" in
        # new: the new release that was live last (LAST_NEW), not merely the newest folder (a failed deployment
        # leaves one behind); before any new release has been live, the newest new_* folder.
        new) if [ -s "$REL/LAST_NEW" ] && [ -d "$REL/$(cat "$REL/LAST_NEW")" ]; then cat "$REL/LAST_NEW"
             else ls -1dt "$REL"/new_*/ 2>/dev/null | head -1 | xargs -r basename; fi ;;
        *) [ -d "$REL/$1" ] && echo "$1" ;;
    esac
}

compose_json() {   # the release's compose file as JSON, resolved against the live folder (its .env, its paths)
    docker compose -p "$PROJECT" --project-directory "$LIVE" -f "$REL/$1/docker-compose.yml" config --format json
}

api_image() {   # the image the release runs for the api service (compose names a build-only service <project>-api)
    compose_json "$1" | python3 -c '
import json, sys
img = json.load(sys.stdin)["services"]["api"].get("image") or sys.argv[1] + "-api"
print(img if ":" in img.rsplit("/", 1)[-1] else img + ":latest")' "$PROJECT"
}

services() { compose_json "$1" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["services"]))'; }

precheck() {   # precheck <release>: everything a switch needs, without changing anything
    local r="$1"
    [ -f "$REL/$r/docker-compose.yml" ] || die "release $r has no docker-compose.yml"
    compose_json "$r" >/dev/null || die "release $r: the compose file does not resolve"
    local img; img=$(api_image "$r")
    docker image inspect "${img%:*}:rel-$r" >/dev/null 2>&1 || die "release $r: its API image ${img%:*}:rel-$r is missing"
    docker ps --format '{{.Names}}' | grep -qx wifitest-db || die "the database container wifitest-db is not running"
    command -v rsync >/dev/null || die "rsync is not installed"
    echo "$img"
}

wait_ok() {   # wait_ok <release>: /health and the front page answer within two minutes
    local i
    for i in $(seq 1 40); do
        if curl -sf -o /dev/null "http://127.0.0.1:$PORT/health" && [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")" = 200 ]; then
            return 0
        fi
        sleep 3
    done
    return 1
}

switch_to() {   # switch_to <release> <revert: 1 = switch back to the previous release on failure>
    local r="$1" revert="$2" from img svcs c svc
    from=$(current)
    img=$(precheck "$r")
    svcs=$(services "$r")
    log "switch $from → $r (api image $img)"

    docker tag "${img%:*}:rel-$r" "$img"
    # Exact mirror of the release; the data folders and .env in the live folder are excluded, so they are kept.
    # grafana/ too: Grafana is not part of a release (a changed dashboard file would overwrite edits made in its UI).
    rsync -a --delete --exclude '/docker-data/' --exclude '/logs/' --exclude '/WiFiTestLogs/' --exclude '/build/' \
        --exclude '/dist/' --exclude '/.env' --exclude '/grafana/' --exclude '__pycache__/' "$REL/$r/" "$LIVE/"

    # Containers of this project the release does not define (the new dashboard's nginx when going back) hold
    # port $PORT: remove them before the api starts.
    for c in $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT"); do
        svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$c")
        grep -qx "$svc" <<<"$svcs" || { log "  remove container $(docker inspect -f '{{.Name}}' "$c") (service $svc)"; docker rm -f "$c" >/dev/null; }
    done
    local up=(api); grep -qx nginx <<<"$svcs" && up+=(nginx)
    if ( cd "$LIVE" && docker compose -p "$PROJECT" up -d --no-deps --force-recreate "${up[@]}" ) && wait_ok "$r"; then
        echo "$r" > "$REL/CURRENT"
        case "$r" in new_*) echo "$r" > "$REL/LAST_NEW" ;; esac
        log "live: $r   http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/"
        return 0
    fi
    log "switch to $r failed; containers:"
    docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '    {{.Names}}: {{.Status}}' | tee -a "$REL/switch.log" >&2
    for c in wifitest-api wifitest-nginx; do
        docker inspect "$c" >/dev/null 2>&1 || continue
        log "  last lines of $c:"
        docker logs --tail 8 "$c" 2>&1 | sed 's/^/    /' | tee -a "$REL/switch.log" >&2
    done
    if [ "$revert" = 1 ] && [ "$from" != unknown ] && [ "$from" != "$r" ]; then
        log "switching back to $from"
        switch_to "$from" 0 && die "switch to $r failed; $from is live again"
    fi
    die "switch to $r failed"
}

drop_web_tables() {
    [ "$(current)" = old ] || die "--drop-web-tables needs the old dashboard live (switch.sh old first)"
    mkdir -p "$ROOT/DB_backups"
    local dump="$ROOT/DB_backups/before_drop_web_tables_$TS.dump"
    log "pg_dump → $dump"
    docker exec wifitest-db pg_dump -U qc -d wifitest -Fc > "$dump"
    [ -s "$dump" ] || die "the dump is empty: nothing dropped"
    log "rollback_web_additions.sql"
    docker exec -i wifitest-db psql -U qc -d wifitest -v ON_ERROR_STOP=1 -q < "$REL/rollback_web_additions.sql"
    log "database structure is the pre-deployment one; test_record rows: $(docker exec wifitest-db psql -U qc -d wifitest -Atc 'SELECT COUNT(*) FROM test_record')"
}

restore_db() {   # restore_db <dump>
    local dump="$1" safety rows
    [ -s "$dump" ] || die "no backup at '$dump'"
    docker exec -i wifitest-db pg_restore -l < "$dump" >/dev/null 2>&1 || die "$dump is not a pg_dump archive"
    if [ "$YES" = 0 ]; then
        echo "This replaces the WHOLE database with $dump; rows written after it was taken are lost." >&2
        read -r -p "Type yes to continue: " a; [ "$a" = yes ] || die "nothing restored"
    fi
    [ "$(current)" = old ] || switch_to old 1
    mkdir -p "$ROOT/DB_backups"
    safety="$ROOT/DB_backups/before_restore_$TS.dump"
    log "restore-db $dump: pg_dump of the database as it is → $safety"
    docker exec wifitest-db pg_dump -U qc -d wifitest -Fc > "$safety"
    [ -s "$safety" ] || die "the safety dump is empty: nothing restored"
    log "rollback_web_additions.sql"
    docker exec -i wifitest-db psql -U qc -d wifitest -v ON_ERROR_STOP=1 -q < "$REL/rollback_web_additions.sql"
    log "pg_restore --clean (one transaction)"
    docker exec -i wifitest-db pg_restore -U qc -d wifitest --clean --if-exists --no-owner --single-transaction \
        --exit-on-error < "$dump" || die "pg_restore failed: the database is as before it (structure of the old dashboard)"
    rows=$(docker exec wifitest-db psql -U qc -d wifitest -Atc 'SELECT COUNT(*) FROM test_record')
    log "restored $dump: test_record rows $rows; the old dashboard is live (switch.sh new to go forward)"
}

status() {
    local r img
    echo "live release: $(current)    'new' means: $(resolve new || true)    project folder: $ROOT"
    echo "releases:"
    for d in "$REL"/*/; do
        r=$(basename "$d")
        img=$(api_image "$r" 2>/dev/null) || img="?"
        printf '  %-16s %-40s image %s\n' "$r" "$(head -1 "$d/RELEASE.txt" 2>/dev/null)" \
            "$(docker image inspect "${img%:*}:rel-$r" >/dev/null 2>&1 && echo "ok (${img%:*}:rel-$r)" || echo "MISSING (${img%:*}:rel-$r)")"
    done
    echo "containers:"
    docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '  {{.Names}}\t{{.Image}}\t{{.Status}}'
    printf 'health: '; curl -s -m 5 "http://127.0.0.1:$PORT/health" || echo "no answer"; echo
}

cmd="${1:-status}"; shift || true
DROP=0; YES=0; ARGS=()
for a in "$@"; do
    case "$a" in --drop-web-tables) DROP=1 ;; --yes) YES=1 ;; *) ARGS+=("$a") ;; esac
done
exec 9>"$REL/.lock"; flock -n 9 || die "another switch is running"
case "$cmd" in
    status) status ;;
    check)
        r=$(resolve "${ARGS[0]:-}") || true; [ -n "${r:-}" ] || die "no release '${ARGS[0]:-}' under $REL"
        img=$(precheck "$r")
        echo "release $r: api image $img, services: $(services "$r" | tr '\n' ' ')"
        echo "live now: $(current). The switch can be made." ;;
    restore-db) restore_db "${ARGS[0]:-}" ;;
    -h|--help|help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0" ;;
    *)
        r=$(resolve "$cmd") || true; [ -n "${r:-}" ] || die "no release '$cmd' under $REL (switch.sh status lists them)"
        [ $DROP = 0 ] || [ "$r" = old ] || die "--drop-web-tables goes with 'old' only"
        switch_to "$r" 1
        [ $DROP = 0 ] || drop_web_tables ;;
esac

#!/usr/bin/env bash
# Every test suite against the local stack, one after another; prints one line per suite and a summary.
#
#   tests/run_all.sh [--quick] [--compat <backup.dump>]
#
#   --quick    skip the long ones (upload boundary tests, the TV rotation pass, the 5F-2 scan wait)
#   --compat   also run tests/db_compat.sh on that production backup (≈ 5 min)
#
# Environment: BASE (default http://127.0.0.1:8000), SAMPLE_ZIP (the delivery zip the upload tests use,
# 5101-251216015 / TN-99_EDM1-IMX6P), SHOTS (screenshot folder, default a temp dir), API_IMAGE (default
# wifitest-api:0.2.0, for the suites that run inside the API image), TWEAK_USER / TWEAK_PASS (default qc / qcpass).
# Logs of every suite land in $LOGS (default a temp dir); a failing suite's tail is printed.
set -u
cd "$(dirname "$0")/.."
BASE=${BASE:-http://127.0.0.1:8000}
SAMPLE_ZIP=${SAMPLE_ZIP:-}
SHOTS=${SHOTS:-$(mktemp -d)}
LOGS=${LOGS:-$(mktemp -d)}
API_IMAGE=${API_IMAGE:-wifitest-api:0.2.0}
QUICK=0
COMPAT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --quick) QUICK=1 ;;
        --compat) COMPAT="$2"; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

declare -a NAMES STATUS
run() {   # run <name> <command…>
    local name="$1"; shift
    local log="$LOGS/$name.log"
    local t0=$SECONDS
    if "$@" >"$log" 2>&1; then
        STATUS+=("ok"); echo "ok    $name ($((SECONDS - t0)) s) — $(grep -E '[0-9]+/[0-9]+' "$log" | tail -1)"
    else
        STATUS+=("FAIL"); echo "FAIL  $name ($((SECONDS - t0)) s) — log: $log"; tail -15 "$log" | sed 's/^/      /'
    fi
    NAMES+=("$name")
}
in_image() {   # a test that needs psycopg2 and the API modules: run it inside the API image
    docker run --rm --network host -v "$PWD/dockerup-essential/api:/app:ro" -v "$PWD/tests:/tests:ro" \
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" \
        -e DATABASE_URL=postgresql://qc:qcpass@127.0.0.1:5432/wifitest -w /app "$API_IMAGE" python3 "/tests/$1"
}

curl -sf "$BASE/health" >/dev/null || { echo "API not reachable at $BASE"; exit 2; }

run i18n               node tests/js/i18n_keys.mjs
run unit_wo_rules      python3 tests/unit_wo_rules.py
run unit_fail_rules    python3 tests/unit_fail_rules.py
run unit_fail_actions  python3 tests/unit_fail_actions.py
run golden             python3 tests/golden.py check "$BASE"
run plan_revert        in_image unit_plan_revert.py
run fail_classify      in_image check_fail_classification.py
run accept_5a          python3 tests/acceptance_phase5a.py "$BASE"
run accept_5b          python3 tests/acceptance_phase5b.py "$BASE"
run accept_5f1         python3 tests/acceptance_phase5f1.py "$BASE"
run accept_5c          python3 tests/acceptance_phase5c.py "$BASE"
run accept_llm_budget  python3 tests/acceptance_llm_budget.py "$BASE"
if [ -n "$SAMPLE_ZIP" ]; then
    run accept_upload  python3 tests/acceptance_upload.py "$BASE" "$SAMPLE_ZIP"
    [ $QUICK = 1 ] || run boundary_upload python3 tests/boundary_upload.py "$BASE" "$SAMPLE_ZIP"
    [ $QUICK = 1 ] || run boundary_ui     python3 tests/boundary_upload_ui.py "$BASE" "$SHOTS/boundary" "$SAMPLE_ZIP"
    run e2e_phase4     python3 tests/e2e_phase4.py "$BASE" "$SHOTS/phase4" "$SAMPLE_ZIP"
else
    echo "skip  upload suites (set SAMPLE_ZIP)"
fi
[ $QUICK = 1 ] || run accept_5f2 python3 tests/acceptance_phase5f2.py "$BASE"      # needs test_uploader (acceptance_upload)
run e2e_screens        python3 tests/e2e_screens.py "$BASE" "$SHOTS/screens"
run e2e_5a             python3 tests/e2e_phase5a.py "$BASE" "$SHOTS/phase5a"
run e2e_5b             python3 tests/e2e_phase5b.py "$BASE" "$SHOTS/phase5b"
run e2e_5f1            python3 tests/e2e_phase5f1.py "$BASE" "$SHOTS/phase5f1"
[ $QUICK = 1 ] || run e2e_5c python3 tests/e2e_phase5c.py "$BASE" "$SHOTS/phase5c"
run e2e_5f2            python3 tests/e2e_phase5f2.py "$BASE" "$SHOTS/phase5f2"
run golden_after       python3 tests/golden.py check "$BASE"
[ -n "$COMPAT" ] && run db_compat tests/db_compat.sh "$COMPAT"

fails=0
for s in "${STATUS[@]}"; do [ "$s" = ok ] || fails=$((fails + 1)); done
echo
echo "$(( ${#STATUS[@]} - fails ))/${#STATUS[@]} suites passed (logs: $LOGS, screenshots: $SHOTS)"
[ $fails = 0 ]

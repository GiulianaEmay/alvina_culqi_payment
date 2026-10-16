#!/usr/bin/env bash
set -euo pipefail
mode="${1:--i}"
test_database="${CULQI_TEST_DATABASE:-culqi19_validation}"
[[ "$test_database" =~ ^culqi19_[a-z0-9_]+$ ]] || { echo 'Only disposable culqi19_* databases are allowed.' >&2; exit 2; }
case "$mode" in -i|-u) ;; *) echo 'Usage: run-addon-tests.sh [-i|-u]' >&2; exit 2 ;; esac
exec 9>/workspace/culqi-runtime/setup.lock
flock -n 9 || { echo 'Runtime setup or tests are already active.' >&2; exit 1; }
log=/workspace/culqi-runtime/odoo-addon-tests.log
if [[ -f "$log" ]]; then cp "$log" "$log.previous"; fi
: > "$log"
export LD_LIBRARY_PATH=/workspace/culqi-runtime/root/usr/lib/x86_64-linux-gnu
exec /workspace/odoo19-venv/bin/python /workspace/culqi-runtime/odoo/odoo-bin \
  --db_host=127.0.0.1 --db_port=55432 --db_user=culqi_test \
  --database="$test_database" \
  --addons-path=/workspace/culqi-runtime/odoo/addons,/workspace/alvina_culqi_payment \
  --data-dir=/workspace/culqi-runtime/odoo-data \
  --http-interface=127.0.0.1 --http-port=58069 --max-cron-threads=0 \
  --without-demo=True --stop-after-init --test-enable \
  --test-tags=/alvina_culqi_payment "$mode" alvina_culqi_payment \
  --logfile=/workspace/culqi-runtime/odoo-addon-tests.log

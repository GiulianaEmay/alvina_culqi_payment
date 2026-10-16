#!/usr/bin/env bash
set -euo pipefail
cd /workspace/alvina_culqi_payment
bash scripts/setup-tests.sh
bash scripts/setup-odoo-runtime.sh
export LD_LIBRARY_PATH=/workspace/culqi-runtime/root/usr/lib/x86_64-linux-gnu
module_state=$(/workspace/culqi-runtime/root/usr/lib/postgresql/17/bin/psql \
  -h 127.0.0.1 -p 55432 -U culqi_test -d culqi19_validation -Atc \
  "SELECT state FROM ir_module_module WHERE name = 'alvina_culqi_payment'")
case "$module_state" in
  installed) test_mode=-u ;;
  uninstalled|'') test_mode=-i ;;
  *) echo 'The addon has a pending module operation; inspect the disposable DB before continuing.' >&2; exit 1 ;;
esac
bash scripts/run-odoo-tests.sh "$test_mode"

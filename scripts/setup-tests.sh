#!/usr/bin/env bash
set -euo pipefail
cd /workspace/alvina_culqi_payment
export UV_CACHE_DIR=/workspace/.cache/uv
if [ ! -x /workspace/culqi-dev-venv/bin/python ]; then
    uv venv /workspace/culqi-dev-venv
fi
uv pip install --python /workspace/culqi-dev-venv/bin/python -r requirements-dev.txt
/workspace/culqi-dev-venv/bin/python -m unittest discover -s tests -v

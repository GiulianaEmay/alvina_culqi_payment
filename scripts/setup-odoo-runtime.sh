#!/usr/bin/env bash
# Disposable Odoo 19 development runtime. Does not alter application repository files.
set -euo pipefail
umask 077
runtime=/workspace/culqi-runtime
repo=${CULQI_REPOSITORY:-/workspace/alvina_culqi_payment}
odoo_revision=5997f8d35b91c5a49048af7785511b8aa388b502
apt_dir=/workspace/.cache/culqi-apt
venv=/workspace/odoo19-venv
mkdir -p "$runtime" /workspace/.cache/uv
exec 9>"$runtime/setup.lock"
flock -n 9 || { echo 'Runtime setup is already active.' >&2; exit 1; }
for tool in git gcc g++ dpkg-deb apt-get apt-cache uv python3 flock; do
  command -v "$tool" >/dev/null || { echo "Required tool is missing: $tool" >&2; exit 1; }
done
. /etc/os-release
[[ "$ID" == debian && "$VERSION_CODENAME" == trixie ]] || {
  echo 'This helper requires Debian 13 trixie; do not apply it to another platform.' >&2; exit 1;
}
[[ "$(dpkg --print-architecture)" == amd64 ]] || { echo 'This helper requires amd64.' >&2; exit 1; }
[[ -f "$repo/alvina_culqi_payment/__manifest__.py" ]] || { echo 'Existing addon checkout was not found.' >&2; exit 1; }
[[ -r /usr/share/keyrings/debian-archive-keyring.gpg ]] || { echo 'Debian archive keyring is missing.' >&2; exit 1; }
mkdir -p "$apt_dir/etc/sources.list.d" "$apt_dir/etc/apt.conf.d" "$apt_dir/etc/preferences.d" "$apt_dir/lists/partial" "$apt_dir/archives/partial" "$runtime/root" "$runtime/bin"
cat > "$apt_dir/apt.conf" <<APTCONF
Dir::Etc "$apt_dir/etc";
Dir::Etc::sourcelist "sources.list";
Dir::Etc::sourceparts "sources.list.d";
Dir::Etc::parts "apt.conf.d";
Dir::State::lists "$apt_dir/lists";
Dir::Cache::archives "$apt_dir/archives";
Debug::NoLocking "true";
APT::Sandbox::User "$(id -un)";
APTCONF
cat > "$apt_dir/etc/sources.list" <<'SOURCES'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
SOURCES
export APT_CONFIG="$apt_dir/apt.conf"
apt-get update
stage=$(mktemp -d "$runtime/deb-stage.XXXXXX")
trap 'rm -rf "$stage"' EXIT
(
  cd "$stage"
  # apt authenticates signed repository metadata and verifies package hashes.
  apt-get download postgresql-17 postgresql-client-17 libpq5 libpq-dev \
    libldap-dev libldap2 libsasl2-dev libsasl2-2 libsasl2-modules-db \
    libllvm19 libxml2 libxslt1.1 libicu76 libssl3t64 liblz4-1 libzstd1 \
    libgssapi-krb5-2 libkrb5-3 libk5crypto3 libcom-err2 libkeyutils1 libkrb5support0
  sha256sum ./*.deb > "$runtime/debian-packages.sha256"
  for package in ./*.deb; do dpkg-deb -x "$package" "$runtime/root"; done
)
cat > "$runtime/bin/pg_config" <<'PGCONFIG'
#!/bin/sh
/workspace/culqi-runtime/root/usr/bin/pg_config "$@" | sed 's|/usr/include|/workspace/culqi-runtime/root/usr/include|g;s|/usr/lib|/workspace/culqi-runtime/root/usr/lib|g'
PGCONFIG
chmod +x "$runtime/bin/pg_config"
if [[ ! -d "$runtime/odoo/.git" ]]; then
  [[ ! -e "$runtime/odoo" ]] || { echo 'Odoo source destination already exists without Git metadata.' >&2; exit 1; }
  git init "$runtime/odoo"
  git -C "$runtime/odoo" remote add origin https://github.com/odoo/odoo.git
  git -C "$runtime/odoo" fetch --depth=1 origin "$odoo_revision"
  git -C "$runtime/odoo" checkout --detach FETCH_HEAD
fi
[[ "$(git -C "$runtime/odoo" rev-parse HEAD)" == "$odoo_revision" ]] || { echo 'Unexpected Odoo revision; inspect existing dependency checkout.' >&2; exit 1; }
[[ -z "$(git -C "$runtime/odoo" status --porcelain)" ]] || { echo 'Odoo dependency checkout contains changes; preserve and inspect them.' >&2; exit 1; }
export UV_CACHE_DIR=/workspace/.cache/uv
[[ -x "$venv/bin/python" ]] || uv venv --python python3.12 "$venv"
export PATH="$runtime/bin:$PATH"
export LD_LIBRARY_PATH="$runtime/root/usr/lib/x86_64-linux-gnu"
export CC=gcc CXX=g++ LDSHARED='gcc -shared'
export CFLAGS="-I$runtime/root/usr/include" LDFLAGS="-L$runtime/root/usr/lib/x86_64-linux-gnu"
uv pip install --python "$venv/bin/python" -r "$runtime/odoo/requirements.txt"
uv pip check --python "$venv/bin/python"
"$venv/bin/python" "$runtime/odoo/odoo-bin" --version
pgbin="$runtime/root/usr/lib/postgresql/17/bin"
if [[ ! -f "$runtime/pgdata/PG_VERSION" ]]; then
  "$pgbin/initdb" -D "$runtime/pgdata" --username=culqi_test --auth-local=trust --auth-host=trust --locale=C.UTF-8 --encoding=UTF8
fi
[[ "$(cat "$runtime/pgdata/PG_VERSION")" == 17 ]] || { echo 'Unexpected PostgreSQL cluster version.' >&2; exit 1; }
mkdir -p "$runtime/socket" "$runtime/odoo-data"
if ! "$pgbin/pg_ctl" -D "$runtime/pgdata" status >/dev/null 2>&1; then
  "$pgbin/pg_ctl" -D "$runtime/pgdata" -l "$runtime/postgresql.log" -o "-h 127.0.0.1 -p 55432 -k $runtime/socket" start
fi
"$pgbin/pg_isready" -h 127.0.0.1 -p 55432
if [[ "$("$pgbin/psql" -h 127.0.0.1 -p 55432 -U culqi_test -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = 'culqi19_validation'")" != 1 ]]; then
  "$pgbin/createdb" -h 127.0.0.1 -p 55432 -U culqi_test culqi19_validation
fi
"$venv/bin/python" "$runtime/odoo/odoo-bin" \
  --db_host=127.0.0.1 --db_port=55432 --db_user=culqi_test \
  --database=culqi19_validation --addons-path="$runtime/odoo/addons,$repo" \
  --data-dir="$runtime/odoo-data" --http-interface=127.0.0.1 --http-port=58069 \
  --without-demo=True --stop-after-init --max-cron-threads=0 \
  -i sale_management,account_payment --logfile="$runtime/odoo-dependencies.log"
echo 'Disposable Odoo 19 runtime is ready. No custom addon or production instance was changed.'

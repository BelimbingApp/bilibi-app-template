#!/usr/bin/env bash
# The combined Domain gates, restoring the committed recipe without resolving it.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
# The development database is never an implicit check target.
: "${BILIBI_CHECK_DATABASE_URL:?Set BILIBI_CHECK_DATABASE_URL to a separate empty disposable PostgreSQL 18 database}"
python3 - <<'CHECK_DATABASE'
import os
import subprocess
from urllib.parse import urlsplit

def identity(value):
    url = urlsplit(value)
    host = url.hostname or 'localhost'
    if host in ('127.0.0.1', '::1'):
        host = 'localhost'
    # Query parameters can override libpq's path, host and port; compare the
    # connection's actual identity below as well as the normal URL shorthand.
    return host, url.port or 5432, url.path

def connected_identity(value):
    result = subprocess.run(['psql', value, '-X', '-v', 'ON_ERROR_STOP=1', '-Atqc',
                             "SELECT current_database(), extract(epoch from pg_postmaster_start_time())"],
                            capture_output=True, text=True, env={**os.environ, 'PGCONNECT_TIMEOUT': '5'})
    return result.stdout.strip() if result.returncode == 0 else None

check_url = os.environ['BILIBI_CHECK_DATABASE_URL']
dev_url = os.environ.get('DATABASE_URL') or 'postgres://bilibi@127.0.0.1:5438/bilibi'
check, dev = connected_identity(check_url), connected_identity(dev_url)
if not check:
    raise SystemExit('Cannot connect to the check database; provide a separate empty disposable PostgreSQL 18 database.')
if identity(check_url) == identity(dev_url) or (dev and check == dev):
    raise SystemExit('Check database is the development database. Choose a separate empty disposable database.')
CHECK_DATABASE
export DATABASE_URL=$BILIBI_CHECK_DATABASE_URL
[[ $(psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc "SELECT current_setting('server_version_num')::int / 10000") == 18 ]] || {
    echo 'Check requires a disposable PostgreSQL 18 database.' >&2; exit 1;
}
[[ $(psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','f')") == 0 ]] || {
    echo 'Check requires an empty disposable database; this database already contains tables or views.' >&2; exit 1;
}
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-${BILIBI_BUILD_DIR:-$root/build}/target}
target=x86_64-unknown-linux-gnu
python3 "$root/scripts/recipe.py" restore
cd "${BILIBI_SOURCE_DIR:-$root/sources}/platform"
npm ci --prefix apps/base/ui/assets --no-audit --no-fund
./scripts/build-assets.sh --check
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --target "$target" --locked -- -D warnings
cargo zigbuild --workspace --release --target "$target.$BILIBI_GLIBC" --locked
python3 "$root/scripts/native_builder.py" --verify-binaries
python3 "$root/scripts/recipe.py" verify
app="$CARGO_TARGET_DIR/$target/release/bilibi-web"
python3 "$root/scripts/recipe.py" status --binary "$app"
"$app" migrate
# This disposable boot uses HTTP loopback, so install it explicitly as Dev.
BILIBI_ENV=development "$app" seed-production
port=${SETUP_CI_PORT:-38971}
BIND_ADDR="127.0.0.1:$port" APP_ORIGIN="http://127.0.0.1:$port" "$app" run &
server=$!
trap 'kill "$server" 2>/dev/null || true; wait "$server" 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
    kill -0 "$server" 2>/dev/null || { echo 'App exited during boot' >&2; exit 1; }
    status=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" || true)
    if [[ $status =~ ^(200|30[1-8])$ ]]; then
        echo "GET / answered $status"
        exit 0
    fi
    sleep 1
done
echo 'GET / did not become ready' >&2
exit 1

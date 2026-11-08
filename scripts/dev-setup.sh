#!/usr/bin/env bash
# Check actual commands, including tools installed during this same invocation.
set -euo pipefail
cd -- "$1"
ready() {
    local tool pg_bin
    pg_bin=${PG18_BIN:-$(command -v pg_config >/dev/null && pg_config --bindir || echo /usr/lib/postgresql/18/bin)}
    for tool in git bash python3 curl ss flock psql pg_isready npm node rustup cargo rustc; do
        command -v "$tool" >/dev/null || return 1
    done
    rustc --version >/dev/null && cargo --version >/dev/null &&
        cargo watch --version >/dev/null 2>&1 && node -e 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)' &&
        npm --version >/dev/null && psql --version >/dev/null &&
        python3 -c 'import tomllib' &&
        { [[ -n ${DATABASE_URL:-} ]] || [[ -x $pg_bin/initdb && -x $pg_bin/pg_ctl ]]; }
}
if ! ready; then
    bash scripts/setup.sh
    # shellcheck source=/dev/null
    if [[ -f $HOME/.cargo/env ]]; then source "$HOME/.cargo/env"; fi
fi
ready || { echo 'Some prerequisites are still unavailable. See .local/setup.log in the Platform checkout.' >&2; exit 1; }

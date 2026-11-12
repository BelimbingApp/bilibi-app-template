#!/usr/bin/env bash
# CI/artifacts use pinned tools; --development may use installed supported Node.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
exec python3 "$root/scripts/native_builder.py" "$@"

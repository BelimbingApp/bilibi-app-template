#!/usr/bin/env bash
# Every local and CI build enters the same pinned native environment.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
exec python3 "$root/scripts/native_builder.py" "$@"

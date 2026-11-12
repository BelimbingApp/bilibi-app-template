#!/usr/bin/env bash
# Compile the saved composition without resolving a new dependency set.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
app_root=${BILIBI_TOOL_APPLICATION_ROOT:-$root}
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-${BILIBI_BUILD_DIR:-$app_root/build}/target}
platform=${BILIBI_SOURCE_DIR:-$app_root/sources}/platform
target=x86_64-unknown-linux-gnu
python3 "$root/scripts/recipe.py" restore
cd "$platform"
npm ci --prefix apps/base/ui/assets --no-audit --no-fund
./scripts/build-assets.sh --check
cargo zigbuild --workspace --release --target "$target.$BILIBI_GLIBC" --locked
python3 "$root/scripts/native_builder.py" --verify-binaries
python3 "$root/scripts/recipe.py" verify
python3 "$root/scripts/native_builder.py" --record-build
python3 "$root/scripts/recipe.py" status --binary "$CARGO_TARGET_DIR/$target/release/bilibi-web"
echo "Built saved release programs in $CARGO_TARGET_DIR/$target/release. Run ./bilibi release to release them (add --tag setup-vX.Y.Z to choose the version yourself)."

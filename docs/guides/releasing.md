# Releasing your application

Build the versions you want to use, check them, then release the saved build record to your servers.

## Build your versions

For example, fix Finance in `sources/platform/apps/domains/finance`, then commit and push it to `origin`. Run:

```sh
./bilibi build
```

Build compares each local Platform, Domain and Extension checkout with the last saved build record. For each part that changed, it asks:

```text
finance has new commits since the last build (abc1234 -> def5678). Build with them? (y/n)
```

Say **yes** to use those commits and save them, or **no** to build that part's saved version. You can choose separately for each part. A newly added part shows `not included` as its previous version. Your local branches stay where they are.

**Commit and push your source edits first.** Build stops for uncommitted edits or commits that cannot be fetched from `origin`. It allows only verified generated composition files in the Platform checkout. `./bilibi check` uses the same choices. With no terminal, a changed part stops the command; `./bilibi build --yes` or `./bilibi check --yes` accepts all new pushed commits in a script.

When you accept a change, the command fetches a candidate from the declared HTTPS remotes without reusing local Git objects. It checks assets, resolves dependencies in the recorded builder and validates the record and lock. It builds or checks a separate checkout of that candidate, and only after the command succeeds does it replace `setup.toml`, `composition/manifest.txt` and `composition/Cargo.lock`. A failed candidate, compilation or check leaves the previous saved set intact. Release is allowed only when the current record matches the record from the last successful build or check; otherwise run `./bilibi build` first.

Each source URL must use HTTPS without credentials and each commit must be a full lowercase 40-character ID. Domains and Extensions also need their declared `id`. Domains and Extensions are optional. Keep each part's `origin` at the URL in the saved record.

Builds with no accepted changes restore the saved dependency lock. They do not choose new dependencies. The release workflow on GitHub always builds only the committed record, even if `--yes` is supplied. It never asks about local sources or updates the record.

Run the combined gates with a **separate empty disposable PostgreSQL 18 database**:

```sh
export BILIBI_CHECK_DATABASE_URL=postgresql://YOUR-USER@127.0.0.1:5432/EMPTY-DISPOSABLE-DATABASE
./bilibi check
./bilibi build
```

`DATABASE_URL` remains your development connection and is never an implicit check target. Check refuses the same database or a database with existing tables or views. Check leaves its migrations and production seed in the disposable database; provide a new empty database on the next run. It seeds that database as a Dev copy, because its boot check uses HTTP on a loopback address, which the Platform allows only for Dev copies. Docker uses host networking to reach it. Set `SETUP_CI_PORT` if the default check port, 38971, is occupied.

`BILIBI_SOURCE_DIR` overrides the source directory; `BILIBI_BUILD_DIR` still overrides outputs and caches. `CARGO_TARGET_DIR` overrides Cargo output. Use absolute paths outside editable sources for outputs. The builder maps all three consistently into its container. Record candidates always use their own directories and ignore these overrides. Builds use separate source checkouts and reuse your output and cache directories.

## Release your setup

**Build in development or GitHub Actions; servers install the finished release.** Before releasing, enable **release immutability** in your application repository's Settings. Protect `main` and the `setup-v*` tags so only release maintainers can change them, and allow GitHub Actions to run.

After the checks pass, start a release with a new application version:

```sh
./bilibi release --tag setup-v1.0.0
```

After the build succeeds, commit `setup.toml`, `composition/manifest.txt` and `composition/Cargo.lock`, review that commit, and merge it into the application's default branch. `./bilibi release` refuses any uncommitted file, a commit outside the remote default branch, or a record that differs from the last successful build or check. It tags that reviewed record and pushes the tag to `origin`, starting **Release setup**. Release never selects or commits source versions.

The setup version belongs to your repository. It is separate from the Platform and Domain versions. **Never move a published tag or replace a published asset.**

**Release setup** runs only for a `setup-vX.Y.Z` tag whose commit is on the repository's default branch. It repeats the combined gates, checks the compiled program's reported composition, packages it, publishes SHA-256 checksums, creates GitHub build provenance for every asset, and creates a draft release through the `release` environment. Only the draft job has `contents: write`; only the attestation job can request an identity token and write attestations. It uploads every asset and compares each digest with GitHub's recorded SHA-256. A rerun refuses an existing release instead of replacing its files.

Final publication also needs an authenticated GitHub CLI session and Node.js 20 or newer on your machine. Install the locked `gh-axi` helper:

```sh
npm ci --prefix scripts/github-tools --ignore-scripts --no-audit --no-fund
export PATH="$PWD/scripts/github-tools/node_modules/.bin:$PATH"
```

The owner publishes the verified draft as a separate final action. This keeps an administration credential out of Actions: checking GitHub's immutability setting requires administration read permission. Before publishing, confirm that every asset shows build provenance from this repository's `.github/workflows/release.yml`. Use your own authenticated `gh-axi` session, with that permission and permission to publish releases. In a clean checkout of the tagged commit, download all draft assets into an empty `dist/` folder:

```sh
git checkout setup-v1.0.0
gh-axi release download setup-v1.0.0 --repo YOUR-ORGANISATION/YOUR-SETUP --dir dist
python3 scripts/publish.py publish --tag setup-v1.0.0 --repository YOUR-ORGANISATION/YOUR-SETUP
```

The script checks the tagged commit, local checksums, GitHub asset digests and repository immutability before publishing. It then requires GitHub to report an immutable release. A `setup-vX.Y.Z-test` tag runs the same build but must stay a draft; delete the draft and tag after testing.

## What the release contains

| Asset | Purpose |
| --- | --- |
| `bilibi-setup-v<version>-x86_64-unknown-linux-gnu.tar.gz` | The existing upload format: root-level `bilibi-web`, `manifest.txt`, `Cargo.lock`, `release.json`, `bundle.json` and `SHA256SUMS`. |
| `bilibi-installer-x86_64-unknown-linux-gnu` | The matching installer, outside the app archive. |
| `bilibi-server-tools.tar.gz` | The matching server installation script, libraries, service templates and waiting page. |
| `recipe.json` | Setup repository, tag and commit; every source URL and commit; Platform, Domains, Extensions, toolchain, builder, target and dynamic dependencies. |
| `builder-packages.txt` | The builder's installed Debian package versions. |
| `publication.json` | The build run link and actual compiler and Node versions. |
| `SHA256SUMS` | SHA-256 for every other release asset. The app archive has a separate checksum list for its payload. |

GitHub stores the signed provenance separately from the release assets. Servers verify each selected asset against that provenance, the application repository and `.github/workflows/release.yml` before they unpack or install it.

`release.json` includes both `reference` and `commit` for containers because the existing upload preview and installer result use those different names. Extension identities are recorded and checked against the program; the current Platform upload preview does not display Extensions.

## Build requirements

Only Linux x86-64 (`x86_64-unknown-linux-gnu`) is supported by this maker today. `rust-toolchain.toml` pins Rust 1.99.0. `builder.toml` pins the Rust and Node image digests, Node 24.6.0, PostgreSQL 18.0, the Debian package snapshot and the runner label. The Dockerfile uses that dated snapshot for Python and records installed packages.

The Debian 12 builder records its glibc version and the binary's dynamic dependencies in the recipe. This is a build baseline, not a claim that every Debian or Ubuntu release has been tested. Check the actual target server's libraries before promotion. Distributed programs do not use `target-cpu=native`.

Archives have sorted entries, fixed modes, zero ownership, recipe-commit timestamps and no gzip timestamp. The workflow repackages the same program and compares every asset digest. This proves deterministic packaging. **The program is built from the recorded recipe; two clean compiler builds have not yet proved identical output bytes.** The workflow records the checked run time; compare a cold run with a later run that restores the cache. Cache keys include the source map, composition, toolchain and builder; cache hits still run every check.

## Where to go next

[Private sources](private-sources.md) covers access for checks. [Installing a release](installing.md) puts the tested archive on a server.

Read the Platform's [server installation guide](https://github.com/BelimbingProject/bilibi/blob/main/docs/server-install.md) for a fresh server and [Software guide](https://github.com/BelimbingProject/bilibi/blob/main/apps/base/software/README.md) for install and Undo. GitHub documents [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) and the [asset digest API](https://docs.github.com/en/rest/releases/assets).

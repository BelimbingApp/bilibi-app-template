# Releasing your application

Check the versions you want to use, then release the saved build record to your servers.

## Check your versions

For example, fix Finance in `sources/platform/apps/domains/finance`, then commit and push it to `origin`. Provide a separate empty disposable PostgreSQL 18 database, then run:

```sh
export BILIBI_CHECK_DATABASE_URL=postgresql://YOUR-USER@127.0.0.1:5432/EMPTY-DISPOSABLE-DATABASE
./bilibi check
```

Check compares each local Platform, Domain and Extension checkout with the last saved build record. For each part that changed, it asks:

```text
finance has new commits since the last build (abc1234 -> def5678). Build with them? (y/n)
```

Say **yes** to use those commits and save them, or **no** to build that part's saved version. You can choose separately for each part. A newly added part shows `not included` as its previous version. Your local branches stay where they are.

**Commit and push your source edits first.** Check stops for uncommitted edits or commits that cannot be fetched from `origin`. It allows only verified generated composition files in the Platform checkout. With no terminal, a changed part stops the command; `./bilibi check --yes` accepts all new pushed commits in a script.

When you accept a change, the command fetches a candidate from the declared HTTPS remotes without reusing local Git objects. It checks assets, resolves dependencies in the recorded builder and validates the record and lock. It builds and checks a separate checkout of that candidate, and only after the command succeeds does it replace `setup.toml`, `composition/manifest.txt` and `composition/Cargo.lock`. A failed candidate, compilation or check leaves the previous saved set intact. Release is allowed only when the current record matches the record from the last successful check; otherwise run `./bilibi check` first.

Each source URL must use HTTPS without credentials and each commit must be a full lowercase 40-character ID. Domains and Extensions also need their declared `id`. Domains and Extensions are optional. Keep each part's `origin` at the URL in the saved record.

Checks with no accepted changes restore the saved dependency lock. They do not choose new dependencies. The release workflow on GitHub always builds only the committed record, even if `--yes` is supplied. It never asks about local sources or updates the record.

Run the combined gates with a **separate empty disposable PostgreSQL 18 database**:

```sh
export BILIBI_CHECK_DATABASE_URL=postgresql://YOUR-USER@127.0.0.1:5432/EMPTY-DISPOSABLE-DATABASE
./bilibi check
```

`DATABASE_URL` remains your development connection and is never an implicit check target. Check refuses the same database or a database with existing tables or views. Check leaves its migrations and production seed in the disposable database; provide a new empty database on the next run. It seeds that database as a Dev copy, because its boot check uses HTTP on a loopback address, which the Platform allows only for Dev copies. Set `SETUP_CI_PORT` if the default check port, 38971, is occupied.

`BILIBI_SOURCE_DIR` overrides the source directory; `BILIBI_BUILD_DIR` still overrides outputs and caches. `CARGO_TARGET_DIR` overrides Cargo output. Use absolute paths outside editable sources for outputs. The native builder uses these paths directly. Record candidates use a separate checkout beside the build directory and share tools and compiler caches. Your editable sources stay untouched.

## Release your setup

**Build in development or GitHub Actions; servers install the finished release.**

### Before your first release

Do these once, in your application repository's Settings on GitHub:

- Enable **release immutability**, so a published release can never be changed.
- Protect `main` and the `setup-v*` tags so only release maintainers can change them.
- Allow GitHub Actions to run.

On your computer, sign in to GitHub with `gh auth login`, using an account that can publish releases and read the repository's settings. Release also needs Node.js 20 or newer with npm; `./bilibi setup` prepares it.

### Release

After check succeeds, commit `setup.toml`, `composition/manifest.txt` and `composition/Cargo.lock` (an adopted application also has `maker.lock` committed, which check covers but never changes). Review that commit and merge it into the application's default branch. Then run one command from the default branch:

```sh
./bilibi release
```

Release walks you through every step and prints what it is doing:

1. **Checks your checkout.** It refuses any uncommitted file, a commit that is not on the remote default branch, a record that differs from your last successful `./bilibi check`, a copy that is behind the remote default branch, and a default branch whose **Check setup** run for this exact commit is missing, still running or did not pass.
2. **Suggests the next version.** For example, after `setup-v1.2.3` it offers `setup-v1.2.4`; the first release is `setup-v1.0.0`. Press Enter to accept it, type another version such as `setup-v1.3.0`, or type `no` to stop without changing anything.
3. **Pushes the tag** to `origin`. This starts the **Release setup** build on GitHub.
4. **Waits for the build** and shows its link. The build takes a while. You can stop waiting with Ctrl+C; the build carries on, and `./bilibi release --tag setup-v1.2.4` continues from where you left off.
5. **Publishes the release.** It downloads every asset into `.local/releases/setup-v1.2.4/`, checks each one against `SHA256SUMS` and GitHub's own digests, checks that the tag and recipe name this commit, and then publishes the draft with `scripts/publish.py`. GitHub must then report the release as immutable.
6. **Tells you what to install.** It prints the exact archive to upload, its SHA-256, and the steps on each server's Software page. [Installing a release](installing.md) explains those steps.

The printed instructions end like this:

```text
Install it on each server, Staging first, then Production:
  1. On the server, open Software. Set GitHub repository to your-company/my-bilibi and accept it
     (once per server; a private repository also needs its read token).
  2. Open Software -> Releases and upload this file:
       /home/you/my-bilibi/.local/releases/setup-v1.2.4/bilibi-setup-v1.2.4-x86_64-unknown-linux-gnu.tar.gz
     SHA-256 ..., checked against SHA256SUMS and GitHub.
  3. Review the versions it lists, then confirm the install with a fresh 2FA code.
```

**If the build fails,** release prints the link to the failed run and stops. If the fix needs no new commit, such as a missing secret, choose **Re-run all jobs** on that run, then run `./bilibi release --tag <the same version>` to continue. A fix that changes files needs a new commit, a new check and a new version.

**In a script,** name the version and confirm it up front: `./bilibi release --tag setup-v1.2.4 --yes`. Without a terminal, release refuses to guess.

The setup version belongs to your repository. It is separate from the Platform and Domain versions. Release never selects or commits source versions, and **never moves a published tag or replaces a published asset.**

A `setup-vX.Y.Z-test` version runs the same build and checks, but stays a draft. Release prints how to delete the draft and its tag after testing.

### What happens on GitHub

**Release setup** runs only for a `setup-vX.Y.Z` tag whose commit is on the repository's default branch. It repeats the combined gates, checks the compiled program's reported composition, packages it, publishes SHA-256 checksums, creates GitHub build provenance for every asset, and creates a draft release through the `release` environment. Only the draft job has `contents: write`; only the attestation job can request an identity token and write attestations. It uploads every asset and compares each digest with GitHub's recorded SHA-256. A rerun refuses an existing release instead of replacing its files.

Publishing is your own action, never the workflow's. This keeps an administration credential out of Actions: checking GitHub's immutability setting requires administration read permission, which `./bilibi release` uses through your own `gh auth login` session. It installs the locked `gh-axi` helper from `scripts/github-tools` under `.local/github-tools` the first time.

Every server checks each asset's build provenance from this repository's `.github/workflows/release.yml` before it unpacks or installs it. To check it yourself, run `gh attestation verify <file> --repo your-company/my-bilibi` on a downloaded file. Downloads stay under `.local/releases/` until you delete them.

## What the release contains

| Asset | Purpose |
| --- | --- |
| `bilibi-setup-v<version>-x86_64-unknown-linux-gnu.tar.gz` | The existing upload format: root-level `bilibi-web`, `manifest.txt`, `Cargo.lock`, `release.json`, `bundle.json` and `SHA256SUMS`. |
| `bilibi-installer-x86_64-unknown-linux-gnu` | The matching installer, outside the app archive. |
| `bilibi-server-tools.tar.gz` | The matching server installation script, libraries, service templates and waiting page. |
| `recipe.json` | Setup repository, tag and commit; every source URL and commit; Platform, Domains, Extensions, toolchain, builder, target and dynamic dependencies. |
| `builder-packages.txt` | The build host's installed package versions (pacman on Arch, dpkg on Ubuntu and Debian). |
| `publication.json` | The build run link, actual Rust, Node, Zig and cargo-zigbuild versions, and build host details. |
| `SHA256SUMS` | SHA-256 for every other release asset. The app archive has a separate checksum list for its payload. |

GitHub stores the signed provenance separately from the release assets. Servers verify each selected asset against that provenance, the application repository and `.github/workflows/release.yml` before they unpack or install it.

`release.json` includes both `reference` and `commit` for containers because the existing upload preview and installer result use those different names. Extension identities are recorded and checked against the program; the current Platform upload preview does not display Extensions.

## Build requirements

Only Linux x86-64 (`x86_64-unknown-linux-gnu`) is supported by this maker today. `rust-toolchain.toml` pins Rust 1.99.0. `builder.toml` pins Node 24.6.0, Zig 0.14.1 and cargo-zigbuild 0.24.1, including download checksums. PostgreSQL 18 runs natively for check.

Install the native prerequisites on Ubuntu or Debian: `sudo apt-get install build-essential binutils python3-venv curl postgresql-client-18`. PostgreSQL 18 packages may need the [PostgreSQL apt repository](https://www.postgresql.org/download/linux/ubuntu/). Run `./bilibi setup` to prepare the tools. Local setup, start and check use installed stable Node 22+ with working npm, matching the Platform asset package and locked dependencies. If unavailable or unsupported, they automatically select the checksum-verified Node archive (including npm) under the build directory. They never change global Node/npm. Rust, Zig and cargo-zigbuild retain their pins. An installed Zig or cargo-zigbuild mismatch still stops preparation.

Arch Linux and Omarchy are supported too. Run `./bilibi setup`; it installs missing native prerequisites with pacman, including rustup, build tools, Python and PostgreSQL 18. Node follows the same local selection policy; Zig keeps the exact version above, independently of Arch's rolling packages. If pacman offers a different PostgreSQL major, setup stops and asks for PostgreSQL 18 packages. Setup never creates a system PostgreSQL cluster.

Local commands and the GitHub release workflow both call `scripts/builder.sh`. That wrapper prepares native tools and records their actual versions and host packages. Internal artifact builds and CI always use the checked pinned Node archive, even when installed Node differs. Local check passes the internal `--development` option. Compiled binary hashes are bound to their build toolchain evidence; packaging requires pinned build evidence matching those bytes, and cannot relabel a local Node build after changing PATH. GitHub rebuilds the reviewed record before packaging. Cargo picks its own job count; set `CARGO_BUILD_JOBS` to limit it on a small machine. It keeps temporary compiler files under the build directory. The release workflow uses no containers; PostgreSQL runs as a disposable native cluster.

Cargo-zigbuild links release programs with Zig for `x86_64-unknown-linux-gnu.2.36`. This keeps the Debian 12 glibc baseline even when your computer uses newer glibc. We keep GNU Linux linkage to preserve the runtime libraries used by the Platform. Both release binaries are checked with `objdump`; any required glibc version above 2.36 fails the build. The recipe records the baseline, the actual required version and dynamic dependencies for each binary. Check the actual target server's libraries before promotion. Distributed programs do not use `target-cpu=native`.

To validate release packaging from a pull request, run the **Release setup** workflow manually on that branch. It builds, checks, packages and attests a `setup-v0.0.0-test` artifact without creating a Git tag on GitHub or publishing a release.

Archives have sorted entries, fixed modes, zero ownership, recipe-commit timestamps and no gzip timestamp. The workflow repackages the same program and compares every asset digest. This proves deterministic packaging. **The program is built from the recorded recipe; two clean compiler builds have not yet proved identical output bytes.** The workflow records the checked run time; compare a cold run with a later run that restores the cache. Cache keys include the source map, composition, toolchain and builder; cache hits still run every check.

## Where to go next

[Private sources](private-sources.md) covers access for checks. [Installing a release](installing.md) puts the tested archive on a server.

Read the Platform's [server installation guide](https://github.com/BelimbingProject/bilibi/blob/main/docs/server-install.md) for a fresh server and [Software guide](https://github.com/BelimbingProject/bilibi/blob/main/apps/base/software/README.md) for install and Undo. GitHub documents [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) and the [asset digest API](https://docs.github.com/en/rest/releases/assets).

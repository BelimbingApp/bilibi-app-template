# Taking maker updates

After one-time adoption, run:

```sh
./bilibi update
```

It checks the official maker first, installs its shared tooling, then fast-forwards your application's configured tracking branch and restores **your application's saved** Platform, Domain and Extension commits. It never chooses the newest components independently, changes `origin`, pushes a branch or opens a pull request. Build still owns choosing new component versions.

There are no maker releases today. The normal stream is `main` at `https://github.com/BelimbingProject/bilibi-app-maker.git`. Each update selects its exact full Git commit, not an invented stable release. The command prints that commit and a SHA-256 of the tooling files. Successful selection is recorded atomically in the checkout's Git metadata at `git rev-parse --git-path bilibi-maker/selected.json`. Status prints the selected identity too.

The root `bilibi` is a standalone Python 3.11 bootstrap. `maker.toml` is application-owned configuration; it declares the official repository, `main` and protocol format 1. Shared runtime, setup and build scripts run from a verified, immutable-by-convention version directory under `git rev-parse --git-path bilibi-maker`. Every invocation compares its file contents and modes with the selected Git objects before executing it. Downloads use Git over HTTPS, verify the object graph, stage a complete version and rename it into place before selection. Code never runs from an incomplete installation, a network response piped to a shell or a command found on PATH. Trust is the official GitHub repository over HTTPS and your local Git configuration, not a separately signed release. Do not configure Git URL rewrites to untrusted servers.

The old version stays available when another is selected. The parent bootstrap keeps the update lock while the new runtime runs, including when application changes require a process handoff. An application's copied scripts cannot replace that runtime halfway through an update. Different application checkouts have separate caches and locks. Normal commands use the selected cache offline; `update` must reach the official stream, even on days when nothing changes.

## One-time adoption

New applications cloned from a maker with this bootstrap can run `./bilibi adopt` after connecting `origin` to their own repository. Review and commit `bilibi`, `maker.toml` and `maker.lock`, then push through the application's usual review process. Future daily work uses only `./bilibi update`.

For an older application with copied tooling, use a **clean checkout**, with no unresolved merge. Preserve any existing work and runtime data first. Point `upstream` at the official maker (add it if absent):

```sh
git remote set-url upstream https://github.com/BelimbingProject/bilibi-app-maker.git
git fetch upstream main
git rev-parse upstream/main
```

Review that exact maker commit. Install only its standalone bootstrap, retaining a copy of the old entry point in Git metadata:

```sh
git show HEAD:bilibi > .git/bilibi-before-adoption
git show upstream/main:bilibi > .git/bilibi-reviewed-bootstrap
# Inspect .git/bilibi-reviewed-bootstrap before executing it.
cp .git/bilibi-reviewed-bootstrap bilibi
chmod +x bilibi
./bilibi adopt
git diff -- bilibi maker.toml
git add bilibi maker.toml maker.lock
git commit -m "Adopt versioned shared maker tooling"
```

These example `.git/` paths assume an ordinary clone. In a linked worktree, obtain paths with `git rev-parse --git-path <name>` instead. Publish the adoption commit through your normal application review. Once it reaches your tracking branch and the checkout is clean, run `./bilibi update`.

Adoption checks copied tracked `scripts/` against the shared official ancestor, or the selected maker commit for unrelated histories. Unmodified older copies are supported when the historical link exists. Customized scripts stop adoption with the exact path; nothing is overwritten. Port such changes deliberately to application configuration or propose them to the maker, then retry. Unrelated histories whose copies differ also need this manual review. An old entry point cannot acquire a new updater by itself: replacing it with the reviewed bootstrap is this one-time boundary. A checkout with existing merge conflicts must be reconciled manually before adopting; do not reset it to make adoption pass.

`setup.toml`, `composition/`, `builder.toml`, `rust-toolchain.toml`, `.env`, the product README and application source remain application-owned. Adoption leaves copied scripts and workflows intact for inspection; `./bilibi` no longer executes those scripts. `maker.lock` records the exact maker commit and tooling SHA-256 an application last adopted or built with. `./bilibi build` and `./bilibi check` rewrite it when the selected maker differs; commit it with the build record. A successful build covers it, so `./bilibi release` tags the maker the build used. CI and release never advance it: `./bilibi tooling` verifies the recorded commit is on official `main`, installs that exact tooling and prints its directory; the maker's own checkout prints itself. The maker's `check.yml` and `release.yml` call scripts only through that directory. Copy those workflows into your application, replacing workflows that call copied `scripts/` directly, before removing the frozen copies. The maker's own checkout without `maker.toml` keeps using local scripts for development. Do not merge maker `main` into an adopted application for routine tooling updates.

## Failures and recovery

This workflow is for the current pre-production development stage. Daily main changes can expose runtime incompatibilities; failures are explicit feedback. It does not deploy, migrate a database, stop a running app or roll back application data. Supported configuration is checked using the existing recipe and composition validators; unknown bootstrap protocol versions are refused before selection. There is no compatibility solver.

- Uncommitted, staged or untracked application/component work stops the application update. The error names the checkout and inspection command. Commit or stash deliberately yourself, then retry; update never stashes, resets or discards your edits.
- Unpushed or diverged application commits stop the fast-forward. Inspect and reconcile/publish them yourself. Unpushed component commits are refused too; published component branches remain available when the saved recipe switches to a detached revision.
- Network failure, missing saved revisions or invalid incoming records stop with the failed command and retry guidance. Existing records and edits stay intact. A partial multi-component switch can be retried: inspect `./bilibi status`, resolve the reported problem, then rerun `./bilibi update` to finish the saved record. There is no blanket data rollback.
- If maker selection succeeds but application update fails, output reports both installed maker and current application commits. The new maker remains selected. Resolve the application's reported problem and rerun the same command; no separate maker command is required.
- Interrupted downloads/installations never select partial directories. Retry the same command. Leftover `install-*` staging directories in this checkout's maker cache can be retained for inspection.
- A damaged cache is refused before execution. Move **only the exact damaged version directory named by the error** aside and retry `./bilibi update`; the Git object store will reconstruct verified files. Do not remove application sources, databases, keys or the whole checkout.
- An incorrect maker remote or unsupported `maker.toml` is refused. Inspect `git remote -v` and the configuration, and correct it deliberately to the official BelimbingProject repository. Never use the obsolete `BelimbingApp` organisation.

The bootstrap protocol deliberately stays small. A future protocol change may require another reviewed bootstrap change; ordinary shared-tooling updates do not rewrite application-owned files or merge copied implementations.

## Older source layout

A workspace still using `build/platform` needs a separate deliberate source-layout migration before adoption. Preserve that directory, its Git history, local edits, keys and database. Do not delete `build/` as disposable output in that layout. Move sources only after verifying paths and stopping that workspace's own processes, then validate with separate disposable data. The updater does not guess how to relocate an older runtime.

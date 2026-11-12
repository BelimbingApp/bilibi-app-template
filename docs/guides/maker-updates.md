# Taking maker updates

Your application uses shared tooling from the maker, such as the setup, build and release scripts, and `./bilibi update` moves it to the newest version in one command.

Here is a normal morning after the one-time [adoption](#one-time-adoption):

```sh
./bilibi update
```

```text
Maker 3f2a1c9e0b7d -> 8c41d07a95e2
Brings in 2 maker commit(s):
  8c41d07 Explain the build cache in setup output
  b19e4f2 Retry Platform downloads once
Compare: https://github.com/BelimbingProject/bilibi-app-maker/compare/3f2a1c9e…...8c41d07a…
Installed maker commit 8c41d07a…; tooling sha256 5d0e…
Updated application and restored saved source commits and composition. Run ./bilibi check to choose new versions.
maker.lock now records maker commit 8c41d07a… (2 in, 0 out). Commit it and open an application pull request: its diff lists the maker commits and the compare link.
```

Notice what happened, in order. Before any new maker code ran, update listed the maker commits it was about to bring in, with a link that shows the full difference on GitHub. Then it installed that maker version, fast-forwarded your application to your team's latest pushed commit, and restored the Platform, Domain and Extension commits saved in `setup.toml`. Finally it wrote the new maker commit into `maker.lock`, which is now an uncommitted change in your checkout.

Notice also what did **not** happen: update did not push, did not open a pull request, and did not choose new Platform or Domain versions (that is still `./bilibi check`).

You may already have questions: why a file has to be committed, what runs in CI meanwhile, and what to do when a new maker version breaks something. We cover each of these below.

## Which command?

There are two ways to take maker changes. They suit different applications:

| | `./bilibi update` | `./bilibi upgrade` |
| --- | --- | --- |
| For | An application that has done the [one-time adoption](#one-time-adoption): it has `maker.toml` and `maker.lock` | An application that still carries copied maker files and has not adopted shared tooling |
| How | Moves `maker.lock` to the newest maker; no maker files are merged | Merges the maker's `main` into a new branch and opens one pull request |
| Result | One changed file, `maker.lock`, in your checkout, which you commit in a pull request | A pull request containing the maker's file changes |

If your application has `maker.lock`, use `update`; `./bilibi upgrade` stops there and says so. If it has not adopted yet, `upgrade` keeps it current until you adopt.

## Why maker.lock?

`maker.lock` is the one record of which maker version your application uses. Because the same file decides what runs everywhere, your laptop, a teammate's fresh clone, CI and the release build all run the same maker commit:

| Where | Which maker runs |
| --- | --- |
| `./bilibi start`, `status`, `check`, `release`, `setup`, `add` | The commit in `maker.lock` in your checkout |
| A teammate's fresh clone | The commit in `maker.lock` they cloned; never the newest maker |
| The pre-commit hook | The commit in `maker.lock`, if it is already installed; otherwise the hook does nothing |
| CI and release (`./bilibi tooling`) | The commit in `maker.lock` in the commit being checked |
| `./bilibi update` | The newest official maker `main`, which it then records in `maker.lock` |

**Only `./bilibi update` moves `maker.lock`.** Check and release never rewrite it. A release tags the record together with `maker.lock`, so you can always see which maker built a release.

## Publishing a maker update

Because `maker.lock` changes only in your checkout, your team and CI keep running the old maker until you publish the change like any other application change:

```sh
git switch -c maker-update
git add maker.lock
git commit -m "Take maker updates"
git push -u origin maker-update
gh pr create --fill
```

The pull request diff for `maker.lock` shows the new commit, the list of maker commits it brings in or removes, and the compare link. Reviewers can open that link to read every maker change, and CI checks the application with the new maker before anyone merges. Once it merges, teammates get the same maker from their next `./bilibi update`.

Until you commit it, a later `./bilibi update` simply works the record out again against the newest maker, so an uncommitted `maker.lock` from an earlier update never blocks the next one.

## Going back to an earlier maker

When a new maker version breaks something for you, go back to the maker you used before:

```sh
./bilibi update --back
```

If you have not committed the new `maker.lock` yet, this returns to the version committed in your application. If you already merged it, this returns to the previous maker commit recorded in your application history. Either way it lists the maker commits it removes, and the resulting `maker.lock` change goes through a pull request like a forward update.

To choose an exact version, give its commit ID, at least 7 characters:

```sh
./bilibi update --to 3f2a1c9
```

**Going back uses the same checks as going forward**: the commit must be on official maker `main`, and it must support this bootstrap. Then tell the maker team what broke, so the next update fixes it.

## What the checks prove

We trust the maker because of how changes reach it: **official maker `main` accepts changes only through pull requests, and every maker pull request is reviewed before it merges.** Your application then reviews each `maker.lock` change in its own pull request. The bootstrap adds checks that keep the code you run equal to that reviewed code:

- It downloads only from `https://github.com/BelimbingProject/bilibi-app-maker.git` over HTTPS, and refuses a different `upstream` remote or a changed cache remote.
- It runs only commits that are on official maker `main`, never an unmerged branch.
- It checks the downloaded Git objects with `git fsck` each time it downloads.
- It installs each maker version into its own folder, in full, before running anything from it.
- It records a SHA-256 of the tooling files in `maker.lock`.

The SHA-256 is a fingerprint, not a signature. It proves that the installed files have not changed since they were installed from that maker commit, and it lets CI check that `maker.lock` names the same files. It says nothing about who wrote the code; that trust comes from review on maker `main` and in your application.

The maker versions live inside your checkout Git folder, at `git rev-parse --git-path bilibi-maker`. Ordinary commands compare the installed folder with the SHA-256 in `maker.lock` and run it without going online. Only `update`, `adopt`, and the first run of a maker version on a machine download anything.

> [!WARNING]
> Git settings in your own configuration, such as a proxy or `http.sslVerify=false`, also apply to these downloads, just as they do for `git clone`. Do not point Git URL rewrites at servers you do not trust.

## Merging maker files with upgrade

Applications that have not adopted shared tooling take maker changes with one command and one pull request you can review. From your default branch, such as `main`:

```sh
./bilibi upgrade
```

It does four things:

1. **Checks that it is safe to start.** Your checkout must have no uncommitted work and be level with `origin`, `upstream` must be exactly `https://github.com/BelimbingProject/bilibi-app-maker.git`, and the GitHub CLI (`gh`) must be installed and signed in. Otherwise it stops, changes nothing, and prints what to fix.
2. **Merges on a new branch.** It fetches the maker's `main`, creates a branch such as `maker-upgrade-1ab6321`, named after the maker commit, and merges the maker into it.
3. **Opens one pull request.** It pushes the branch to `origin` and opens a pull request in your repository. The description lists the maker commits and links to the same changes in the maker.
4. **Returns you to your default branch**, unchanged.

Nothing from the maker runs on your computer during the upgrade. **Merge that pull request with a merge commit, not a squash.** A squash drops the link to the maker, and the next upgrade brings the same changes in again. When there is nothing new, upgrade says "Already up to date with the maker" and changes nothing.

If the merge stops on a conflict, upgrade stops on the new branch before pushing and lists the conflicted files. Keep what you need from both sides (for `setup.toml` and `composition/`, keep yours), run `git add --all` and `git commit --no-edit`, then run `./bilibi upgrade` again. The message also prints the commands that undo the merge.

Upgrade does not move the Platform, Domains and Extensions in `setup.toml` to newer releases; choose new versions with `./bilibi check`, as [Releasing your application](releasing.md) explains.

## One-time adoption

New applications cloned from a maker with this bootstrap run `./bilibi adopt` after connecting `origin` to their own repository. Review and commit `bilibi`, `maker.toml` and `maker.lock`, then push through the usual review process of your application. From then on, daily work uses `./bilibi update`.

For an older application with copied tooling, start from a **clean checkout** with no unresolved merge, and keep a copy of any work and runtime data first. Point `upstream` at the official maker (add it if it is missing):

```sh
git remote set-url upstream https://github.com/BelimbingProject/bilibi-app-maker.git
git fetch upstream main
git rev-parse upstream/main
```

Review that exact maker commit. Then install only its standalone bootstrap, keeping a copy of the old entry point in Git metadata:

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

These example `.git/` paths assume an ordinary clone; in a linked worktree, get each path with `git rev-parse --git-path <name>` instead. Publish the adoption commit through your normal review. Once it reaches your tracking branch and the checkout is clean, run `./bilibi update`.

Adoption compares your copied `scripts/` with the official maker history. Unmodified older copies adopt cleanly. Customized scripts stop adoption with the exact path, and nothing is overwritten, so you can move such changes into application configuration or propose them to the maker, then retry. An old entry point cannot get a new updater by itself, which is why replacing it with the reviewed bootstrap is this one deliberate step. Reconcile any existing merge conflicts by hand first; do not reset the checkout to make adoption pass.

`setup.toml`, `composition/`, `builder.toml`, `rust-toolchain.toml`, `.env`, the product README and application source stay yours. Adoption leaves the copied scripts and workflows in place for inspection, but `./bilibi` no longer runs those scripts. The maker workflows `check.yml` and `release.yml` call scripts only through `./bilibi tooling`, so copy them into your application, replacing workflows that call copied `scripts/` directly, before you remove the frozen copies. Do not merge maker `main` into an adopted application for routine tooling updates. The maker checkout itself has no `maker.toml` and keeps using its own scripts for development.

## Failures and recovery

This workflow is for the current development stage, before production use. It does not deploy, migrate a database, stop a running app or roll back application data. Each failure says what stopped and what to do next:

- **Uncommitted, staged or untracked work** in the application or a component stops the application step and names the checkout. Commit or stash it yourself, then rerun. Update never stashes, resets or discards your edits; the only file it rewrites is `maker.lock`.
- **Unpushed or diverged application commits** stop the fast-forward. Publish or reconcile them yourself. Unpushed component commits are refused too.
- **A network failure** stops update before anything changes, and `maker.lock` stays as it was. Other commands keep working offline with the installed maker.
- **The maker step succeeds but the application step fails**: the output shows both commits, and `maker.lock` keeps the new maker. Fix the reported problem and rerun the same command, or use `./bilibi update --back`.
- **An interrupted download or installation** is never used. Rerun the same command. Leftover `install-*` folders in the maker cache can stay for inspection.
- **A changed installed folder** is refused before anything runs. Move **only the folder named in the error** aside and rerun; the command installs it again from Git. Do not remove application sources, databases, keys or the whole checkout.
- **A `maker.lock` with a commit that is not on official `main`**, or a SHA-256 that does not match its files, is refused, and nothing runs. Restore it from your application history with `git restore maker.lock`, or choose a version with `./bilibi update --to`.
- **A wrong maker remote or an unsupported `maker.toml`** is refused. Inspect `git remote -v` and the configuration, and set it deliberately to the official BelimbingProject repository. Never use the obsolete `BelimbingApp` organisation.
- **A maker version that needs a newer bootstrap** (a different tooling format) is refused, and `maker.lock` stays as it was. A future protocol change comes with a reviewed bootstrap change.

## Older source layout

A workspace that still uses `build/platform` needs a separate, deliberate source-layout migration before adoption. Keep that directory, its Git history, local edits, keys and database, because in that layout `build/` is not disposable output. Move sources only after checking paths and stopping the processes of that workspace, then validate with separate disposable data. The updater does not guess how to move an older runtime.

## Where to go next

[Running your application](running.md) lists every command. [Releasing your application](releasing.md) explains how a release records the build and `maker.lock`.

# Bilibi app maker

The Bilibi app maker is the starting copy for your own application built on Bilibi. For example, you can combine the Bilibi Platform, Finance and your own private Extension in one application. The application records the exact versions of these parts, so you can release it to your servers and take Bilibi updates later.

**Status:** version 0.1, in active development.

## What the app maker does

The Platform has two layers of shared modules for the application you want to build. Base has no business logic: it is the plumbing, such as sign-in, permissions and audit, so every application includes it. Core holds the modules that deal with business records, such as companies, people and products. Because every business is unique, Core modules are on demand: you take only the Core modules that the Domains and Extensions in your application use. A Domain is one business area, such as Finance, whereas an Extension adds your own features. See [ADR 25](https://github.com/BelimbingProject/bilibi/blob/main/docs/architecture/decisions/25-core-modules-on-demand.md) for the reasoning.

This is where the Bilibi app maker comes in. It takes the Platform and the Domains and Extensions you choose, links only the Core modules they use, and records the exact version of every part. It then builds your application into the smallest footprint possible, with no bloat. You get an application that serves exactly what you need, and you can release the same versions to your servers.

## When to use it

Use Bilibi app maker to run your own Bilibi on your servers. For developing and contributing to Bilibi, use the [Platform repository](https://github.com/BelimbingProject/bilibi). 

> [!NOTE]
> In your application, replace this introduction and **Start here** with your own introduction. The linked guides describe your application as they are.

## Start here

1. Clone the maker into a folder named after your product, prepare it, then start it:

```sh
git clone https://github.com/BelimbingProject/bilibi-app-maker my-bilibi
cd my-bilibi
./bilibi setup
./bilibi start
```

Your copy opens at `https://bilibi.localhost`. If you run more than one Bilibi on this computer, give each its own name with `BILIBI_DEV_HOST=<name>.localhost` in `.env` beside `./bilibi`; see [Settings](docs/guides/settings.md).

Leave `./bilibi start` running; it rebuilds and reloads when the code changes. Run `./bilibi update` any time, including in another terminal while start is running, to bring your application and sources to their saved commits. After the one-time adoption below, it first moves `maker.lock` to the newest maker and lists the maker commits, so you can publish that change in a pull request.

Setup records the official maker in `maker.toml` and `maker.lock`, then prepares sources and prerequisites such as Rust, PostgreSQL and Node. It does not build, start services or migrate a database. Repeated setup uses the same lock; `./bilibi update` deliberately takes newer maker and application versions. The first start builds Bilibi, so it takes a while. On the first start, Bilibi opens in your browser at the sign-in page. Later starts print its address. Sign in as `ai@agent.my` / `bilibi-dev`. You now have the Platform on its own: sign-in, companies, people, users, permissions and audit.

2. Add the business features you want. A Domain is one business area, such as Finance; an Extension adapts or connects one. The first `./bilibi start` lists them, with a line about each, before it builds anything. Move with the arrow keys and tick with space, or type an item's number, then press Enter and `y` to add them. To see the list again later:

```sh
./bilibi choose
```

If you already know the name, add it directly and start again:

```sh
./bilibi add finance
./bilibi start
```

The **Software** page in Bilibi lists the same Domains and Extensions, and shows the exact `add` command for each one.

3. Make it yours. Your application gets its own private repository on GitHub, for example `your-company/my-bilibi`. Before you push, choose your application's licence. A private application usually starts with `Copyright (c) <year> <owner>. All rights reserved.` in its root `LICENSE`; an open-source application should use a licence you choose. The maker's MIT licence is not your application's licence, so keep it separately. If your copy still has a root `LICENSE` (you cloned the maker directly), move it with:

```sh
mkdir -p licenses
git mv LICENSE licenses/bilibi-app-maker.txt
```

Put your chosen licence text in the root `LICENSE`, then save it as a commit so the push below carries it:

```sh
git add LICENSE licenses
git commit -m "Set application licence"
```

Then make the repository yours with one command. It needs the [GitHub CLI](https://cli.github.com/) (`gh`), signed in with `gh auth login`:

```sh
./bilibi own your-company/my-bilibi
```

```text
Step 1 of 3: your repository your-company/my-bilibi on GitHub
  $ gh repo create your-company/my-bilibi --private
Step 2 of 3: point origin at your repository and keep the maker as upstream
  $ git remote rename origin upstream
  $ git remote set-url upstream https://github.com/BelimbingProject/bilibi-app-maker.git
  $ git remote add origin https://github.com/your-company/my-bilibi.git
  $ gh repo set-default your-company/my-bilibi
Step 3 of 3: push main to your repository
  $ git push --set-upstream origin main
```

Each step prints the command it runs, so you can see what happened. If `your-company/my-bilibi` already exists and is empty, `own` uses it instead of creating it; if it already has commits, `own` stops, so nothing there is overwritten. `gh repo set-default` matters more than it looks: without it, `gh` would open your later pull requests against the maker. If a step fails, fix the cause it shows and run the same command again; it finishes the remaining steps.

`own` is for a fresh clone. If `origin` already points somewhere other than the maker, it changes nothing and prints the commands to connect your repository by hand.

Now adopt the maker's shared tooling, and push that too:

```sh
./bilibi setup
git add bilibi maker.toml maker.lock
git commit -m "Adopt versioned shared maker tooling"
git push
```

Your folder now has two remotes. `origin` is your application, where you push. `upstream` is the maker. `./bilibi update` takes its later tooling improvements without merging maker files, and records the maker version in `maker.lock`. `./bilibi upgrade` moves the Platform to its newest recommended release, and in an application that still carries copied maker files it also merges them, through one pull request. See [Take maker updates](docs/guides/maker-updates.md).

> [!WARNING]
> Clone the maker as shown. A copy made any other way, such as a downloaded ZIP file, has none of the maker's history. First setup requires a Git checkout and verifies retained managed scripts against official maker history. Customized managed scripts need an explicit port before setup can continue.

4. Check and release the versions you tested. Commit and push changes in each part's own repository first. Check uses supported installed Node, with automatic private fallback; GitHub builds releases with pinned tools. Provide a separate empty PostgreSQL 18 database for check (see the release guide):

```sh
./bilibi check
./bilibi release
```

Check asks about each part with new commits. Say yes to use them and save the build record only after all checks pass, or no to build its saved version. Commit the successful build record and merge it into your application's default branch. Release then suggests the next version, pushes its tag, waits for GitHub to build exactly those reviewed versions, publishes the release, and prints the file to upload on each server's Software page. [Release your setup](docs/guides/releasing.md#release-your-setup) and [Install a release](docs/guides/installing.md#install-a-release) explain each step.

## Guides

- [Settings](docs/guides/settings.md): local addresses, databases and settings for each instance.
- [Running your application](docs/guides/running.md): requirements, workspace, commands and commit safeguards.
- [Releasing your application](docs/guides/releasing.md): build, check and release your application.
- [Installing a release](docs/guides/installing.md): put a tested release on Staging and Production.
- [Private sources](docs/guides/private-sources.md): `./bilibi connect-private-sources` lets checks read your private Domains and Extensions.
- [Taking maker updates](docs/guides/maker-updates.md): `./bilibi update` for adopted applications (`maker.lock`, going back, one-time adoption, recovery) and `./bilibi upgrade` for merging maker files and moving the Platform.
- [Application repositories](docs/guides/about.md): where changes belong and what the maker provides.

## File length

CI warns about a Rust file over 800 lines that is new or got longer in your pull request. It fails only when the pull request body gives no reason for a listed file.

- Under `## File length` in the pull request body, add one line per listed file: `<path>: keep: <reason>` or `<path>: split: <what was split along which responsibility>`.
- Split by responsibility (one screen, one job), never by line count and never into `part1`/`part2`.
- Run `python3 scripts/file-length-ratchet.py` to see the list locally; it compares your branch with `origin/main`.
- Files under `fixtures/`, `examples/`, `migrations/` and `generated/` are skipped.
- CI runs it as `.github/workflows/file-length.yml`; editing the body re-runs it. The same script is copied unchanged from the Bilibi Platform into every Bilibi repository.

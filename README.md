# Bilibi app maker

The Bilibi app maker is the starting copy for your own application built on Bilibi. For example, you can combine the Bilibi Platform, Finance and your own private Extension in one application. The application records the exact versions of these parts, so you can release it to your servers and take Bilibi updates later.

**Status:** version 0.1, in active development.

## When to use it

Use Bilibi app maker to run your own Bilibi on your servers. For developing and contributing to Bilibi, use the [Platform repository](https://github.com/BelimbingProject/bilibi). 

> [!NOTE]
> In your application, replace this introduction and **Start here** with your own introduction. The linked guides describe your application as they are.

## Start here

1. Clone the maker into a folder named after your product, and start it:

```sh
git clone https://github.com/BelimbingProject/bilibi-app-maker my-bilibi
cd my-bilibi
./bilibi start
```

Your copy opens at `https://bilibi.localhost`. If you run more than one Bilibi on this computer, give each its own name with `BILIBI_DEV_HOST=<name>.localhost` in `.env` beside `./bilibi`; see [Settings](docs/guides/settings.md).

Leave `./bilibi start` running; it rebuilds and reloads when the code changes. Run `./bilibi update` any time, including in another terminal while start is running, to bring sources to their saved commits.

The first start installs what is missing, such as Rust, PostgreSQL and Node, and builds Bilibi, so it takes a while. On the first start, Bilibi opens in your browser at the sign-in page. Later starts print its address. Sign in as `ai@agent.my` / `bilibi-dev`. You now have the Platform on its own: sign-in, companies, people, users, permissions and audit.

2. Add the business features you want. A Domain is one business area, such as Finance:

```sh
./bilibi add finance
./bilibi start
```

The **Software** page in Bilibi lists what is available, and shows the exact `add` command for each one.

3. Make it yours. Create an empty private repository on GitHub, for example `your-company/my-bilibi`. Before you push, choose your application's licence. A private application usually starts with `Copyright (c) <year> <owner>. All rights reserved.` in its root `LICENSE`; an open-source application should use a licence you choose. The maker's MIT licence is not your application's licence, so keep it separately. If your copy still has a root `LICENSE` (you cloned the maker directly), move it with:

```sh
mkdir -p licenses
git mv LICENSE licenses/bilibi-app-maker.txt
```

Put your chosen licence text in the root `LICENSE`, then save it as a commit so the push below carries it:

```sh
git add LICENSE licenses
git commit -m "Set application licence"
```

Then connect your repository and push:

```sh
git remote rename origin upstream
git remote add origin https://github.com/your-company/my-bilibi.git
gh repo set-default your-company/my-bilibi
git push -u origin main
```

Your folder now has two remotes. `origin` is your application, where you push. `upstream` is the maker, so you can take its later improvements: see [Take maker updates](docs/guides/maker-updates.md#take-maker-updates).

> [!WARNING]
> Clone the maker as shown. A copy made any other way, such as a downloaded ZIP file, has none of the maker's history, and maker updates can then no longer merge.

4. Build and release the versions you tested. Commit and push changes in each part's own repository first. These commands use the pinned native build tools:

```sh
./bilibi build
./bilibi release --tag setup-v1.0.0
```

Build asks about each part with new commits. Say yes to use them and save the build record only after the build succeeds, or no to build its saved version. Commit the successful build record and merge it into your application's default branch. Release then creates the protected tag and starts the GitHub release build. GitHub uses exactly those reviewed versions.
To install it on a server, follow [Release your setup](docs/guides/releasing.md#release-your-setup) and [Install a release](docs/guides/installing.md#install-a-release).

## Guides

- [Settings](docs/guides/settings.md): local addresses, databases and settings for each instance.
- [Running your application](docs/guides/running.md): requirements, workspace, commands and commit safeguards.
- [Releasing your application](docs/guides/releasing.md): build, check and release your application.
- [Installing a release](docs/guides/installing.md): put a tested release on Staging and Production.
- [Private sources](docs/guides/private-sources.md): give checks access to private repositories.
- [Taking maker updates](docs/guides/maker-updates.md): merge maker improvements and update older workspaces.
- [Application repositories](docs/guides/about.md): where changes belong and what the maker provides.

## File length

CI warns about a Rust file over 800 lines that is new or got longer in your pull request. It fails only when the pull request body gives no reason for a listed file.

- Under `## File length` in the pull request body, add one line per listed file: `<path>: keep: <reason>` or `<path>: split: <what was split along which responsibility>`.
- Split by responsibility (one screen, one job), never by line count and never into `part1`/`part2`.
- Run `python3 scripts/file-length-ratchet.py` to see the list locally; it compares your branch with `origin/main`.
- Files under `fixtures/`, `examples/`, `migrations/` and `generated/` are skipped.
- CI runs it as `.github/workflows/file-length.yml`; editing the body re-runs it. The same script is copied unchanged from the Bilibi Platform into every Bilibi repository.

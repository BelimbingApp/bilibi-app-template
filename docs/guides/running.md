# Running your application

Run your application on your own machine with `./bilibi start`.

## Run an existing application

Clone your application's repository, then run:

```sh
./bilibi start
```

The same command works with or without an AI agent. It obtains missing sources, installs commit safeguards, prepares missing prerequisites, and starts the existing development launcher. Bilibi opens in your browser at the sign-in page when it is ready; sign in as `ai@agent.my` / `bilibi-dev`. If no browser opens, open the address the launcher prints. Set `BILIBI_OPEN_BROWSER=0` to stop it opening. Local HTTPS may need manual trust; the launcher keeps its loopback HTTP fallback.

Use Ubuntu, Debian, Arch Linux or Omarchy, including Ubuntu or Debian on WSL2. You need Git, Bash, Python 3.11 or newer, network access and installation privileges. Setup may ask for your sudo password. Ubuntu 22.04 and Debian 11 ship older Python; install Python 3.11 or newer as `python3`, or use Ubuntu 24.04 or Debian 12.

On Arch and Omarchy, setup installs missing prerequisites with pacman and performs a full system upgrade before installing packages. It uses rustup for the pinned Rust toolchain and downloads the same checked Node 24 and Zig versions used on Ubuntu and Debian. PostgreSQL must be version 18. An installed Node or Zig version that differs from the recipe stops setup with the required version; select that version on your PATH and rerun setup. The Platform's scripts prepare the remaining development tools, including cargo-watch. See [Release your setup](releasing.md#build-requirements) for the exact pins.

## Your workspace

```text
my-bilibi/                         # application Git repository
├── bilibi                         # standalone stable bootstrap
├── maker.toml                     # shared tooling source, after adoption
├── setup.toml                     # URLs and exact component commits
├── composition/
│   ├── manifest.txt               # saved component identity
│   └── Cargo.lock                 # saved application dependency lock
├── scripts/                       # frozen legacy copy after adoption
├── sources/                       # ignored, valuable editable clones
│   └── platform/                  # independent Platform Git repository
│       └── apps/
│           ├── domains/finance/   # independent Domain Git repository
│           └── extensions/        # optional independent Extension clones
├── build/                         # ignored outputs and caches
└── .local/                        # ignored workspace state
```

| Change | Repository to commit in |
| --- | --- |
| Application versions, manifest, lock, automation and documentation | Application root |
| Platform source and its unmounted generated composition | `sources/platform/` |
| Domain or Extension source | That component's own Git root |
| Build outputs, caches, keys, runtime data and local state | Never commit |

**Keep `sources/`: these are working repositories, not disposable build output.** Domains and Extensions remain ordinary nested clones at their existing Platform mount paths. They are neither submodules nor source symlinks. Older Platform launchers use an ignored `sources/platform/target` output link pointing into `build/target`; setup refuses to replace an existing target path.

`./bilibi status` shows every Git root, branch, local change and difference from the saved versions. Start and status show one line when parts have new commits since the last build. Startup runs your local branches and edits. It does not update `setup.toml` or describe an older build record as the running development version.

The Platform keeps its existing runtime locations: `.local/dev` contains development keys, `.local/postgres18` contains the private development database, and existing media and configuration paths remain unchanged. Environment variables retain their existing meaning. Workspace `.local/` contains command coordination and temporary build checkouts and record candidates; runtime data is not moved there.

## Commands

Run commands from the application's root folder. `./bilibi help` shows the same list. Your branches, staged files and uncommitted edits are always kept.

| Command | What it does | When to use it |
| --- | --- | --- |
| `./bilibi start` | Installs anything missing, fetches the sources, runs the application, and opens it in your browser | Every time you want to run your application |
| `./bilibi add <name>` | Adds a Domain or Extension, such as `finance`. You can also paste the `add` command that the Software page shows. | When you want a new business feature. Run `start` again afterwards. |
| `./bilibi status` | Lists the Platform, Domains and Extensions, with their branches, your uncommitted changes, and any difference from the saved versions | When you want to see what you have changed |
| `./bilibi update` | Updates shared maker tooling first, then gets your team's changes and restores the saved source versions | Before you start work, to match your team |
| `./bilibi check` | Asks about new commits, builds and tests against an empty test database, then saves your choices if it passes. | Before a release |
| `./bilibi build` | Asks about new commits, builds the release program, then saves your choices if it succeeds. | When you make a release; see [Release your setup](releasing.md#release-your-setup) |
| `./bilibi release --tag setup-vX.Y.Z` | Requires the committed successful build record and starts the GitHub release workflow. Refuses other uncommitted application files, or a record that has no successful build or check. | After build and check pass |
| `./bilibi setup` | Installs the tools and fetches the sources, without starting | When you want everything ready in advance |
| `./bilibi help` | Shows the command list | Any time |

`./bilibi new` belongs to the Platform repository. In an application repository, you already own the application recipe.

After one-time adoption, run `./bilibi update` to update shared maker tooling first and receive changes published to your application branch. It restores `sources/` to the commits saved in the updated `setup.toml` and restores the saved dependency lock. **Build and check ask before saving new versions.** Update refuses real edits, staged files, untracked files and unpushed commits, and prints the affected path and next step. It can replace verified generated composition files. It never stashes your work or forces a checkout. An application branch needs a remote upstream; source commits must be published to their declared `origin`.

Copy the install command from **Administration › System › Software** and run it from your application root:

```sh
./bilibi add finance --kind domains --source https://github.com/BelimbingProject/bilibi-dom-finance --commit <recommended-commit>
```

Give all three flags together. The commit must be a full lowercase 40-character ID. Finance mounts at `sources/platform/apps/domains/finance`; Extensions mount at `sources/platform/apps/extensions/<id>`. Without these flags, `add` uses the local Platform catalogue and the repository's default branch, as before.

An existing checkout at the requested commit keeps its branch and edits. If it is at another commit, `add` refuses and prints the requested commit and fetch/checkout steps. Save your work before following those steps. A different origin, occupied path or mismatched component descriptor also causes a refusal. If a new clone lacks the requested commit, it stays available for inspection and the message explains how to retry. Run `./bilibi start` to try the component, then commit and push your component work before `./bilibi build` saves your choice.

Repeated setup does not fetch existing clones, so an unavailable remote cannot discard or block work already present. It never resets, cleans, switches or removes an existing checkout. A saved-version mismatch prints the path and next action. Build and check stop for uncommitted edits, except for verified generated composition files. They build saved commits in separate checkouts. GitHub uses only the committed build record.

## Commit safeguards

Setup installs a repository-local Platform pre-commit hook that runs its own `scripts/discover-modules.py --check`. This rejects staged mounted composition and foreign path packages in the Platform lock. The application pre-commit hook validates the staged recipe, manifest and lock together; its application lock is allowed in Git. CI also validates the saved set because local hooks can be bypassed.

An existing executable pre-commit hook is preserved as `pre-commit.before-bilibi` in the same hook directory and runs first. Its failure stops the commit. Installation is repeatable. The guard finds its repository when it runs, so renaming or moving a checkout does not break it; an unmodified guard from an earlier release is replaced in place, while your own edits to an installed guard are left unchanged with a recovery message. It does not change `core.hooksPath` or global Git configuration. Tracked or symlink hooks, and hook directories outside this repository, are refused so setup cannot rewrite source files or shared hooks. For a shared hook path, configure a local hook directory (for example `git config --local core.hooksPath .git/hooks` in an ordinary clone), copy the hooks you want to retain there, inspect their relative-path assumptions, then rerun `./bilibi setup`. For a symlink hook, copy its target into that local directory as an ordinary executable first.

## Daily maker and application updates

After [one-time shared-tooling adoption](maker-updates.md#one-time-adoption), `./bilibi update` selects official maker `main` by exact commit first, then updates your application and its saved component set. It prints the maker commit and tooling SHA-256, plus the current application commit. It never pushes or opens a pull request. Local edits or unpushed commits stop the application step with recovery guidance.

## Where to go next

[Settings](settings.md) explains addresses and databases. [Releasing your application](releasing.md) covers saving versions and publishing.

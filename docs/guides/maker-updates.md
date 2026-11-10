# Taking maker updates

Merge improvements from the maker into your application with one command and one pull request you can review.

## Take maker updates

Your application has two remotes:

- **`origin`**: your application repository, where you push and open pull requests.
- **`upstream`**: the maker, `https://github.com/BelimbingProject/bilibi-app-maker.git`, where the wrapper, scripts, checks and these guides come from.

The checkouts in `sources/` have only `origin`: the URL saved in `setup.toml`, which build and check verify before saving versions. Never repoint it. To propose a change to a component you cannot push to, add your fork as another remote, for example `fork`, and push your branch there.

Once per clone, add the maker and keep the GitHub CLI on your own repository:

```sh
git remote add upstream https://github.com/BelimbingProject/bilibi-app-maker.git
gh repo set-default YOUR-ORGANISATION/YOUR-APPLICATION
```

Without the second line, the GitHub CLI sends `gh pr create` and other commands to the maker, because it prefers a remote named `upstream`.

To take the maker's changes, run one command from your default branch, such as `main`:

```sh
./bilibi upgrade
```

It does four things:

1. **Checks that it is safe to start.** Your checkout must have no uncommitted work and be level with `origin`, `upstream` must be exactly the maker URL above, and the GitHub CLI (`gh`) must be installed and signed in. Otherwise it stops, changes nothing, and prints what to fix.
2. **Merges on a new branch.** It fetches the maker's `main`, creates a branch such as `maker-upgrade-1ab6321`, named after the maker commit, and merges the maker into it. Git keeps your own changes and brings in only what the maker changed since your last merge.
3. **Opens one pull request.** It pushes the branch to `origin` and opens a pull request in your repository with the GitHub CLI. The description lists the maker commits and links to the same changes in the maker, so you can read every line before you accept it.
4. **Returns you to your default branch**, unchanged.

Nothing from the maker runs on your computer during the upgrade. The new scripts first run in your pull request's checks, and reach your team only after you merge.

**Merge that pull request with a merge commit, not a squash.** A squash drops the link to the maker, and the next upgrade brings the same changes in again. After it merges, run `./bilibi update`.

When there is nothing new, upgrade says "Already up to date with the maker" and changes nothing.

### When the merge stops on a conflict

A conflict happens only where the maker and you changed the same lines. Upgrade then stops on the new branch, before anything is pushed, and lists the conflicted files. To finish:

1. Open each listed file and keep what you need from both sides. For `setup.toml` and the files in `composition/`, keep yours: they record your application's own versions. The message prints the command for this.
2. Save the result with `git add --all`, then `git commit --no-edit`.
3. Run `./bilibi upgrade` again. It sees the finished merge, pushes it and opens the pull request.

To give up instead, run the three commands that the message prints: they undo the merge, return to your default branch and delete the new branch.

### What upgrade does not do yet

Upgrade keeps the maker's parts current. It does not yet move the Platform, Domains and Extensions in `setup.toml` to newer releases, or show the upgrade notes from those releases. That is the rest of [#15](https://github.com/BelimbingProject/bilibi-app-maker/issues/15). Until then, choose new versions with `./bilibi build`, as [Releasing your application](releasing.md) explains.

### By hand

Upgrade runs ordinary Git commands, and you can run them yourself:

```sh
git switch -c maker-sync main
git fetch upstream
git merge upstream/main
git push -u origin maker-sync
gh pr create --fill
```

Use `git merge`, not `git pull` or a rebase, which would rewrite your history onto the maker's.

**If your history is not linked to the maker yet**, upgrade stops and says so, and `git merge upstream/main` refuses with "refusing to merge unrelated histories". First bring your files level with a maker commit by hand, keeping your own changes. Then record that commit as merged without changing any file, and merge that pull request with a merge commit too:

```sh
git merge --allow-unrelated-histories -s ours <maker-commit>
```

Only record a commit whose changes you have really taken: Git will never offer them again.

## Move from BelimbingApp to BelimbingProject

The GitHub organisation was renamed from `BelimbingApp` to `BelimbingProject`. The old name is no longer ours and could be registered by someone else, so never trust it: no remote, URL, source or `setup.toml` entry should point at `BelimbingApp`. If yours does, change it:

1. Point the Platform in `setup.toml` at `https://github.com/BelimbingProject/bilibi.git`.
2. Point the maker remote at the new organisation:

   ```sh
   git remote set-url upstream https://github.com/BelimbingProject/bilibi-app-maker.git
   ```

3. Point each Domain or Extension URL you added at `BelimbingProject`, for example in `setup.toml` and in `./bilibi add` commands, then run setup again.
4. Check that nothing still uses the old name: `grep -rn BelimbingApp .` should print nothing except this section.

## Update an older application workspace

Keep the old checkout and runtime data until a parallel workspace has been used successfully. First record the old paths, remotes, branches, commits, staged and unstaged changes, untracked files, configuration and active processes. Inspect keys and database locations without printing secrets.

Create a separate application clone with the updated maker commands and your reviewed recipe and composition. Run setup there. Validate with a separate database and local data paths. Transfer or commit source edits deliberately in their owning repositories; setup does not migrate the old `build/platform` clone. Do not delete the old `build/`: older layouts keep valuable sources there.

To adopt a clean existing clone manually, stop only that workspace's own processes by PID, verify the resolved source and destination paths, and move `build/platform` to an absent `sources/platform` on the same filesystem. Preserve its `.git`, nested component repositories, local files and runtime data together. Inspect an existing `target/` directory and deliberately move its outputs into `build/target` before setup creates its output link. Run status and setup, then validate with disposable data before changing editor or service paths. Roll back by restoring those recorded paths and the old application checkout; retain its database and keys.

Maker updates are ordinary reviewed application changes; [Take maker updates](#take-maker-updates) shows how. After those changes reach your application's remote branch, `./bilibi update` pulls them into a clean workspace. Update does not import changes from the maker repository; `./bilibi upgrade` does, through a pull request.

## Where to go next

[Running your application](running.md) covers daily work. [Releasing your application](releasing.md) covers publishing the updated application.

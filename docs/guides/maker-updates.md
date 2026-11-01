# Taking maker updates

Merge improvements from the maker into your application on a branch you can review.

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

To take the maker's changes, merge them on a branch and open a pull request:

```sh
git switch -c maker-sync main
git fetch upstream
git merge upstream/main
git push -u origin maker-sync
gh pr create --fill
```

Git keeps your own changes and brings in only what the maker changed since the last merge. It stops on a conflict only where both sides changed the same lines; fix those, then `git commit`. Use `git merge`, not `git pull` or a rebase, which would rewrite your history onto the maker's. **Merge that pull request with a merge commit, not a squash**: a squash drops the link to the maker, and the next merge brings the same changes in again.

**If your history is not linked to the maker yet**, `git merge upstream/main` refuses with "refusing to merge unrelated histories". First bring your files level with a maker commit by hand, keeping your own changes. Then record that commit as merged without changing any file, and merge that pull request with a merge commit too:

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

Maker updates are ordinary reviewed application changes; [Take maker updates](#take-maker-updates) shows how. After those changes reach your application's remote branch, `./bilibi update` pulls them into a clean workspace. It does not import changes directly from the maker repository.

## Where to go next

[Running your application](running.md) covers daily work. [Releasing your application](releasing.md) covers publishing the updated application.

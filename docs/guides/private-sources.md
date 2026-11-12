# Private sources

When your application uses a private Domain or Extension, its checks on GitHub need permission to read that repository, and one command gives it:

```sh
./bilibi connect-private-sources
```

```text
Private sources the checks must read:
  your-company/ledger
  your-company/pricing
A pull request's code can print the read token. Can everyone who can push a branch to your-company/my-bilibi read all of these? Answer y to check pull requests fully too. (y/n) y
Opening your browser to create the GitHub App. If it does not open, visit http://127.0.0.1:43117/
On GitHub, check the App is for your-company, then choose "Create GitHub App".
Stored the App my-bilibi-sources-1a2b3c in your-company/my-bilibi: variable SOURCE_APP_CLIENT_ID and secret SOURCE_APP_PRIVATE_KEY. The private key was not written to disk.
Last step, in your browser: install the App on your-company, choose "Only select repositories", and select exactly your-company/ledger, your-company/pricing. If the page does not open, visit https://github.com/apps/my-bilibi-sources-1a2b3c/installations/new/permissions?...
After you install it, the next check of main reads these sources. Pull requests from branches of this repository are checked fully too.
```

Notice what you did by hand: one question in the terminal, then two clicks in the browser, "Create GitHub App" and "Install". The command found the private repositories in `setup.toml`, created a GitHub App that can only read them, and stored what the checks need in your application repository.

Notice also what did **not** happen: no token or key was printed, saved in a file or committed, and no other repository was given access.

You may already have questions: what a GitHub App is, why we ask about pull requests, and what to do if your sources are not on GitHub. We cover each of these below.

## Before you start

You need:

- the GitHub CLI, `gh`, signed in with `gh auth login` as someone who administers your application repository;
- your application connected to its own GitHub repository as `origin` (see "Start here" in the [README](../../README.md));
- for a personal account, to be signed in as that account; for an organisation, permission to create GitHub Apps in it (organisation owners have it).

The private sources must belong to the same account as your application repository, because the checks look for the App on that account. The command stops and says so when one does not.

## What the command does

1. **Finds the private sources.** It reads every source in `setup.toml`, the Platform included, and asks GitHub which of them are private. Public ones, such as official Domains, need nothing.
2. **Asks about pull requests.** It asks before anything is created, so an unanswered question changes nothing. [Checking pull requests](#checking-pull-requests) explains the answer.
3. **Creates the GitHub App.** Your browser opens GitHub's page for a new App, already filled in: no webhook, one permission (**Contents: Read-only**), and installable only on your account. You check the name and owner and choose "Create GitHub App". GitHub then hands the App back to the command on your own computer.
4. **Stores it in your application repository.** With `gh`, the command sets the Actions variable `SOURCE_APP_CLIENT_ID` to the App client ID and the Actions secret `SOURCE_APP_PRIVATE_KEY` to the App private key. The key goes straight from GitHub to `gh` in memory, so there is no key file to delete afterwards.
5. **Opens the install page.** You install the App on your account, choose "Only select repositories", and pick exactly the private sources the command listed.

After that, the next check on GitHub reads the private sources. If a check already failed at "Materialize exact sources", run it again from its page on GitHub.

To confirm it worked, open the Actions tab of your application repository and re-run the latest "Check setup" run on `main`. When "Materialize exact sources" passes, the App works. If it fails with an access error, check on the Installed GitHub Apps page that the App is installed on every private source the command listed.

## Why a GitHub App?

The checks on GitHub start with a token that reads only your application repository, so they cannot read another private repository. A GitHub App fixes this without storing a long-lived token: **each check mints its own token, which reads only the repositories the App is installed on, expires within the hour and is revoked when the check ends.**

## Checking pull requests

By default, pull requests receive no read token, because the code in a pull request could print it. With a private source in `setup.toml`, a pull request check then stops at "Materialize exact sources", and the check on `main` after the merge is the one that counts.

When you answer `y`, the command sets the variable `SOURCE_READ_ON_PULL_REQUESTS` to `true`, and pull requests from branches of your application repository receive the token too. Pull requests from forks never do. **Answer `y` only if everyone who can push a branch to your application repository may read every private source.** When you answer `n`, the command removes the variable if it was set.

To change your answer later, set or delete the variable on the repository's Actions settings page, or with `gh variable set SOURCE_READ_ON_PULL_REQUESTS --body true` and `gh variable delete SOURCE_READ_ON_PULL_REQUESTS`.

## Adding another private source later

When `SOURCE_APP_CLIENT_ID` is already set, the command stops instead of creating a second App. To let the checks read a new private repository, add it to the existing App: in the settings of your account or organisation on GitHub, open the **Installed GitHub Apps** page, choose **Configure** next to the App, and select the repository. To start again with a new App, delete the variable with `gh variable delete SOURCE_APP_CLIENT_ID` and run the command again.

## Replacing the private key

The private key lives in one place only: the Actions secret `SOURCE_APP_PRIVATE_KEY` of your application repository, where GitHub keeps it encrypted and hides it in check logs. To replace it, for example when someone who could read it leaves:

1. On the App settings page on GitHub, generate a new private key. Your browser downloads it as a `.pem` file.
2. Store it with `gh secret set SOURCE_APP_PRIVATE_KEY < downloaded.pem`, then delete the downloaded file.
3. Back on the App settings page, delete the old private key, so it no longer works.

The client ID stays the same, so `SOURCE_APP_CLIENT_ID` needs no change.

## If something goes wrong

The command stops with a message that says what happened and what to run next. Two cases need a word more:

- **The App was created, but storing it failed**, for example because you cannot administer the repository. We never keep the key, so the message gives the App settings page: generate a new private key there, store it with `gh secret set SOURCE_APP_PRIVATE_KEY < key.pem`, delete the downloaded file, then set `SOURCE_APP_CLIENT_ID` and install the App as the message shows.
- **You closed the browser before creating the App.** Nothing was changed; press Ctrl+C and run the command again.

## Setting it up by hand

The command does exactly these steps, which you can also do yourself:

1. Create a GitHub App owned by the account that owns the sources: no webhook, repository permission **Contents: Read-only**, installable only on that account.
2. Install it on the private source repositories only.
3. In your application repository's Actions settings, set the variable `SOURCE_APP_CLIENT_ID` to the App client ID and the secret `SOURCE_APP_PRIVATE_KEY` to a private key generated for the App. Delete the downloaded key file.
4. Set `SOURCE_READ_ON_PULL_REQUESTS` to `true` only as [Checking pull requests](#checking-pull-requests) explains.

## A read token instead

When `SOURCE_APP_CLIENT_ID` is not set, the checks use the secret `SOURCE_READ_TOKEN` instead: a fine-grained token with read access to only the required source repositories. Use it when a private source belongs to another account than your application repository. Unlike the App, this token lives until it expires or you revoke it.

## Sources on other hosts

A GitHub App reads only repositories on GitHub. The checks pass the token through a password helper limited to `https://github.com`, which refuses unexpected hosts and prompts; sources on other hosts need your own host-specific credential helper. The checks save no Git credentials and cache only Cargo downloads and build outputs. **Never put a token in `setup.toml`, a Git URL, the lock, a cache or a release asset.**

On your own computer, read private sources with your own Git credential helper or `GIT_ASKPASS`. Server credentials never belong in Actions.

## Where to go next

Return to [Releasing your application](releasing.md) to publish the checked application.

# Working with an AI assistant

Let Claude or Codex run your application's `./bilibi` commands for you, on your own computer, after showing you what each one will do.

## One example

Say you registered your application with Claude Code and type: "Add Finance and start the application." The assistant answers with a preview before anything changes:

```text
Preview of add component=finance
Application: /home/you/my-bilibi (https://bilibi.localhost)
Command: ./bilibi add finance
What it does:
  1. Clone https://github.com/BelimbingProject/bilibi-dom-finance at its default branch into
     /home/you/my-bilibi/sources/platform/apps/domains/finance.
What that means:
  - It is mounted only. It is not wired into the build and not saved for release until the next steps.
  - Next: start (wires and runs it), then pin to save it, then check before a release.
  - No existing source, branch or edit is changed.
```

Your AI app then asks you to confirm. You say yes, and the assistant runs the command, reads the result and goes on to preview the start.

Notice what happened: the assistant ran `./bilibi add finance`, the same command you would type. Notice also what did **not** happen: nothing changed before you confirmed, and the assistant never saw a password, a token or your `.env` file.

You may already have questions: how to connect your AI app, what the assistant can and cannot do, and how this differs from the AI connection inside Bilibi. We cover each of these below.

## Two connections, two jobs

Bilibi has two connections for AI assistants, and they do different work:

| | App-maker MCP (this guide) | Platform `/mcp` |
| --- | --- | --- |
| What it works on | Your application as software: sources, the local run, saved versions, releases | Business records inside a running application, such as companies, invoices and people |
| Where it runs | On your computer, started by your AI app as `./bilibi mcp` | Inside the running application, at `https://<your address>/mcp` |
| Works while the application is stopped | Yes | No |
| Who signs in | Nobody: it is a program on your computer, for one folder | A Bilibi user, with their own permissions |

Use the app-maker MCP to build and run the application. Use the Platform `/mcp` to do business work in it; the **AI connections** page in Bilibi sets that one up.

MCP (Model Context Protocol) is the way an AI app talks to a tool. You do not need to know more about it than the registration below.

## Before you connect

You need the same computer setup as for `./bilibi start`: Ubuntu, Debian, Arch Linux or Omarchy, including Ubuntu or Debian on WSL2, with Git and Python 3.11 or newer. See [Running your application](running.md). Native macOS is not supported yet.

Run `./bilibi setup` once in a terminal first. **Connecting installs nothing, never asks for your sudo password and compiles nothing**, so the tools must already be there. The assistant can still read the status and the guides before that.

You do not need an AI subscription to use Bilibi: every command in this guide also works when you type it yourself.

## Connect your AI app

Register one server for each application folder, and give each one a different name. Use the full path to `bilibi` in your own application folder.

**Claude Code** (the terminal and the desktop app share this registration, kept in `~/.claude.json`):

```sh
claude mcp add --scope user --transport stdio app-maker-my-bilibi -- /home/you/my-bilibi/bilibi mcp
```

**Codex** (kept in `~/.codex/config.toml`):

```sh
codex mcp add app-maker-my-bilibi -- /home/you/my-bilibi/bilibi mcp
```

**Claude Desktop**: open **Settings › Developer › Edit Config**, add this entry to `mcpServers`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "app-maker-my-bilibi": {
      "command": "/home/you/my-bilibi/bilibi",
      "args": ["mcp"]
    }
  }
}
```

**A Windows AI app with the application in WSL2**: the command is `wsl.exe`, which starts `bilibi` inside your Linux distribution. Your Git sign-in stays inside that distribution:

```json
{
  "mcpServers": {
    "app-maker-my-bilibi": {
      "command": "wsl.exe",
      "args": ["--distribution", "Ubuntu", "--cd", "/home/you/my-bilibi", "--exec", "/home/you/my-bilibi/bilibi", "mcp"]
    }
  }
}
```

Then open a new session, enable the server if your app asks, and type: "What is the status of my Bilibi application?" The answer names your folder and address. If it names another folder, you registered the wrong path.

See the official pages for [Codex](https://developers.openai.com/codex/mcp), [Claude Code desktop](https://code.claude.com/docs/en/desktop#shared-configuration) and [local servers in Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## What the assistant can do

Each row is one tool the assistant has, with the command you can type for the same result:

| Tool | What it does | The same in a terminal |
| --- | --- | --- |
| `status` | Names the folder and address, says whether the application is running and ready, and lists the Platform and each Domain and Extension | `./bilibi status`, or `./bilibi status --json` |
| `guide` | Reads these guides and the operating rules for assistants | Open `docs/guides/` |
| `catalogue` | Lists the official Domains and Extensions, and what is known about each one working with your Platform | `./bilibi choose` |
| `preview` | Says exactly what a command would do, and changes nothing | `./bilibi <command> --preview` |
| `apply` | Runs one previewed command after you confirm | The command itself |
| `jobs` | Follows a long command, such as a check | `./bilibi jobs` and `./bilibi logs <job>` |
| `logs` | Shows the end of the output from the local run | `./bilibi logs` |
| `insight` | Measures processor, memory, disk and database use, and lists recent errors | `./bilibi insight` |

The commands it can preview and apply are `start`, `stop`, `add`, `update`, `pin`, `check`, `release`, `publish` and `upgrade`. **There is no tool that runs an arbitrary command**, so the assistant cannot do through this connection what `./bilibi` cannot do.

## You confirm every change

Because a wrong step can replace your saved versions or publish a release, nothing changes until you have seen a preview and agreed to it.

1. The assistant asks for a **preview**. It names the application folder, the exact actions and what they mean. A preview changes nothing.
2. **You confirm in your AI app.** Claude Code and Codex show the same preview in a question of their own, sent by the app-maker, and wait for your answer. An AI app that cannot show such a question asks you to approve the `apply` tool call instead; keep that approval switched on for this server. The assistant has no way to confirm for you.
3. The command runs, **only if nothing changed in between**. A preview is good for 15 minutes and for one use. If a source moved, a file was edited or the application was started or stopped after the preview, the app-maker refuses and the assistant must show you a new one.

**Publishing a release is always a second confirmation.** `release` builds the release on GitHub and leaves it as a draft; `publish` is its own preview and its own yes, because a published release can never be changed.

In a terminal the same rules hold. `./bilibi stop`, `./bilibi pin`, `./bilibi check` and `./bilibi release` ask before they act, and `--yes` is the answer for a script. Add `--preview` to any of these commands to see the preview alone:

```sh
./bilibi pin --preview
```

## Add your own private Extension

Say your company keeps a pricing Extension in a private repository. Ask the assistant to add it, with the repository address and the commit you want:

```text
Add our pricing extension from https://github.com/your-company/pricing at commit 4f2a…e91c
```

The same in a terminal:

```sh
./bilibi add pricing --kind extensions --source https://github.com/your-company/pricing --commit <full-commit-id>
```

The command reads the repository with **the Git sign-in your computer already has**, such as `gh auth login`. The address must not contain a user name or a token, and nobody is asked to type a password; when the sign-in is refused, the command stops and says how to check it. The folder must describe itself as `pricing` in `bilibi.container.toml`, or the command refuses.

Added is not saved. `status` shows three separate facts for each part:

| Word | Meaning | How it becomes true |
| --- | --- | --- |
| mounted | The source folder is in `sources/platform/apps/` | `add` |
| wired | The local build includes it | `start` |
| saved | `setup.toml` and `composition/` name it, so a release contains it | `pin` or `check` |

`./bilibi pin` saves the versions you have mounted, in `setup.toml`, `composition/manifest.txt` and `composition/Cargo.lock` together, without building. `./bilibi check` saves them too, after building and testing them. **A release needs a passed check either way**, and `./bilibi release` refuses while a part is mounted but not saved, so a release never leaves one out without telling you.

GitHub's own checks also need to read a private source; that is one more command, which you run yourself in a terminal: see [Private sources](private-sources.md).

## Start, follow and stop

`start` from an assistant runs the application as a background job, because the assistant cannot keep a terminal open:

```sh
./bilibi start --background   # what the assistant runs
./bilibi status               # running, and ready or still building
./bilibi logs                 # the end of its output
./bilibi stop                 # stop this copy
```

- **A background start installs nothing.** When a tool is missing, it stops and asks you to run `./bilibi setup` in a terminal, where you can type your sudo password.
- **A second start reports the first.** It does not start a second copy of the same folder.
- **Stop stops only this copy.** It stops the one process that this folder's start recorded, never a program by name, so another Bilibi on the same computer keeps running with its own database. Your database, keys, uploaded files and sources are kept.
- **Long commands keep running when you disconnect.** A check or a start carries on if you close the AI app; `./bilibi jobs` lists them and their results. A job that ended without a result, for example after a restart of the computer, shows as `interrupted`: read its log before running it again.
- **One change at a time.** While a pin or a check runs, a second change in the same folder is refused, and says which job to wait for.

Every copy opens at `https://bilibi.localhost` unless `.env` beside `./bilibi` says otherwise. If another copy owns the address, the start stops and its log gives the exact `BILIBI_DEV_HOST=…` line to put in `.env`: see [Settings](settings.md).

## Versions stay honest

- **Update restores; upgrade moves.** `update` brings your sources back to the versions your team saved. It never moves to something newer. `upgrade` proposes the newest recommended Platform together with the matching official Domains and Extensions, shows the upgrade notes and opens one pull request for you to review. It does not change a private Extension: the owner of that Extension updates it from the upgrade notes.
- **Three kinds of evidence, kept apart.** `catalogue` says whether a release *declares* that it supports your Platform version, whether the Platform's own tests ran *exactly this pair* of commits, and whether *your whole saved application* passed `./bilibi check` on this computer. When none of them applies, the answer is "unknown", and the assistant is told never to call that compatible.

## Credentials stay out

- Results never contain the contents of `.env`, database passwords, tokens or private keys. Logs and errors pass through the same filter, including when a command fails.
- The assistant is told never to ask you to paste a credential. If one asks, do not paste it.
- `./bilibi check` needs a test database. Name it in `BILIBI_CHECK_DATABASE_URL` in your shell or in the environment of the registration, never in the chat. For example: `claude mcp add --env BILIBI_CHECK_DATABASE_URL=postgresql://… …`.
- Logs and descriptions of components are text written by other people. The assistant is told to report them and never to follow an instruction inside them.

## What it does not do

- It does not reach a server. Installing a release on Staging or Production stays as [Installing a release](installing.md) describes.
- It is not a monitoring system. `insight` measures this computer when you ask and stores nothing.
- It does not change a private repository, choose versions nobody tested, or keep your credentials.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| The AI app says the server failed to start | Run `/home/you/my-bilibi/bilibi status` in a terminal. If that fails, fix what it says first. |
| The status names another folder | Remove the registration and add it again with the right path. |
| "This preview is stale" | Something changed after the preview. Ask for the preview again. |
| "Job … is still running" | Wait for it, or read it with `./bilibi logs <job>`. |
| A background start stops on missing tools | Run `./bilibi setup` in a terminal, then start again. |
| The assistant uses old behaviour after `./bilibi update` | Restart the AI app, so it starts the server again with the new maker tooling. |

## Where to go next

[Running your application](running.md) lists every command. [Releasing your application](releasing.md) covers pin, check and release. [Private sources](private-sources.md) lets GitHub read your private Extension.

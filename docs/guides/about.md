# Application repositories

Your application has its own repository for its versions, automation and documentation.

## Where do my changes go?

| You changed… | Commit it in | Send it to |
| --- | --- | --- |
| Your application: its recipe, automation or docs | This folder | `origin`, your repository |
| Bilibi itself | `sources/platform/` | A pull request to [`BelimbingProject/bilibi`](https://github.com/BelimbingProject/bilibi), pushed from your fork |
| A Domain or Extension | Its folder under `sources/platform/apps/` | That component's own repository |

In `sources/`, leave each folder's `origin` as it is: `./bilibi` checks it against `setup.toml`. To send a pull request to a repository you can't push to, add your fork as another remote, such as `fork`, and push there.

## Which repository?

Bilibi has three kinds of repository:

| Repository | What it holds | Start there to |
| --- | --- | --- |
| `bilibi-app-maker` (this one) | Where every application starts: its starting files and the `./bilibi` commands | Make your own product |
| [`bilibi`](https://github.com/BelimbingProject/bilibi) | Bilibi's source code: the Platform | Change Bilibi itself, or build a Domain or Extension for everyone |
| Your application, such as `your-company/my-bilibi` | One product: the Bilibi, Domain and Extension versions it uses, and its releases | Work on your product, once you have made it |

You clone the maker, and your clone becomes your application. It keeps the maker's working parts as they are, such as the `./bilibi` commands, the checks and the release scripts. You replace its sample parts with your own, such as the `YOUR-ORGANISATION` names in these guides. After one-time adoption, shared tooling is a versioned dependency invoked by the standalone root bootstrap. `maker.lock` records the maker version, and `./bilibi update` moves it to the newest maker before updating the application and its saved component set. An application that still carries copied maker files takes maker changes with `./bilibi upgrade`, which merges them through one pull request: see [Take maker updates](maker-updates.md).

If you started in `bilibi` instead, `./bilibi new <name>` there turns what you built into an application like this one.

## What the maker will do

Today the maker gives you a running Bilibi, a list to choose its business features from (`./bilibi choose`, also offered on the first start), and the commands to build, check and release it. Some steps are still manual, such as making the clone your own or connecting private Domains. The maker will take these over, one command each:

| Step | Today | Planned |
| --- | --- | --- |
| Make it yours | Four Git commands, in [Start here](../../README.md#start-here) | `./bilibi own <owner/name>` ([#12](https://github.com/BelimbingProject/bilibi-app-maker/issues/12)) |
| Use private Domains | Set up a GitHub App by hand, in [Private sources](private-sources.md#private-sources) | One command ([#13](https://github.com/BelimbingProject/bilibi-app-maker/issues/13)) |
| Release and install | `./bilibi release --tag setup-vX.Y.Z` starts the release; [publish the draft and install it](releasing.md#release-your-setup) | Combine the remaining steps ([#14](https://github.com/BelimbingProject/bilibi-app-maker/issues/14)) |
| Stay current | `./bilibi update` moves `maker.lock` to the newest maker, then updates the application and saved source versions; you publish `maker.lock` in a pull request. `./bilibi upgrade` merges maker files into an application that has not adopted shared tooling | Automatic component recommendations and upgrade notes remain separate work ([#15](https://github.com/BelimbingProject/bilibi-app-maker/issues/15)) |

## Where to go next

[Running your application](running.md) covers the workspace and commands. [Taking maker updates](maker-updates.md) keeps your application’s tools current.

# Installing a release

Install the same tested release on Staging and Production.

## Install a release

Set the Software page's **GitHub repository** setting to your own application repository, for example `your-company/my-bilibi`. The server reads its releases from that repository.

Download the `bilibi-setup-v1.0.0-x86_64-unknown-linux-gnu.tar.gz` asset from your own repository. Download `SHA256SUMS` too. The server checks both the SHA-256 and GitHub build provenance from your repository's `.github/workflows/release.yml` before it unpacks the archive. A damaged file, a hand-uploaded replacement or an asset built by another workflow is refused and the running release stays in service.

On the target Bilibi instance, open **Software → Releases**, upload that archive, review it, and confirm the install with a fresh 2FA code. Keep the same downloaded archive for Staging and Production. The existing installer handles backup, migration, switching, health checks, recovery and Undo.

GitHub's automatic **Source code** archives are not installable releases. The separate `bilibi-installer-x86_64-unknown-linux-gnu` and `bilibi-server-tools.tar.gz` assets are for initial server setup or deliberate administrator upgrades. Ordinary application installs do not update the shared installer.

An uploaded file must carry valid GitHub build provenance too. Set and accept the application repository on the Software page before uploading; a private repository also needs its read token. If verification fails, download the asset from the release again or inspect the failed **Release setup** run. Do not bypass the check or rebuild on the server.

## Where to go next

[Settings](settings.md) explains each instance’s configuration. The Platform’s [server installation reference](https://github.com/BelimbingProject/bilibi/blob/main/docs/server-install.md) covers a fresh server and its [Software reference](https://github.com/BelimbingProject/bilibi/blob/main/apps/base/software/README.md) covers install and Undo.

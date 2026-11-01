# Installing a release

Install the same tested release on Staging and Production.

## Install a release

Set the Software page's **GitHub repository** setting to your own application repository, for example `your-company/my-bilibi`. The server reads its releases from that repository.

Download the `bilibi-setup-v1.0.0-x86_64-unknown-linux-gnu.tar.gz` asset from your own repository. Download `SHA256SUMS` too and verify the archive against GitHub's recorded asset digest or [release verification](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity).

On the target Bilibi instance, open **Software → Releases**, upload that archive, review it, and confirm the install with a fresh 2FA code. Keep the same downloaded archive for Staging and Production. The existing installer handles backup, migration, switching, health checks, recovery and Undo.

GitHub's automatic **Source code** archives are not installable releases. The separate `bilibi-installer-x86_64-unknown-linux-gnu` and `bilibi-server-tools.tar.gz` assets are for initial server setup or deliberate administrator upgrades. Ordinary application installs do not update the shared installer.

An uploaded file remains operator supplied. Its checksum detects damaged bytes; **a checksum is not a signature or independent proof of who built it**. Use your configured repository over HTTPS, verify GitHub's digest, and test the exact archive on Staging before Production.

## Where to go next

[Settings](settings.md) explains each instance’s configuration. The Platform’s [server installation reference](https://github.com/BelimbingProject/bilibi/blob/main/docs/server-install.md) covers a fresh server and its [Software reference](https://github.com/BelimbingProject/bilibi/blob/main/apps/base/software/README.md) covers install and Undo.

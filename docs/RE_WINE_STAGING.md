# Linux RE toolbox: Wine, Wine64 and WineHQ Staging

The KITT full Linux RE guest is an isolated Docker container, **not** a virtual machine.
The full and wine profiles now include two genuine Wine installations on Debian
Trixie (amd64, with i386 enabled only inside the image):

| Command | Provenance and purpose |
| --- | --- |
| `wine` | Debian's standard Wine launcher, supplied by the `wine` and `wine64` packages |
| `wine64` | Debian's 64-bit native Wine loader (`/usr/lib/wine/wine64`) |
| `wine-staging` | Real WineHQ Staging amd64 loader in `/opt/wine-staging/bin`, **not** a renamed Debian Wine |

The WineHQ package index is authenticated with an explicit per-repository
`Signed-By` key and its expected full public-key fingerprint is checked during
build. Installed package versions are recorded in
`/usr/share/kitt/re-wine-packages.lock`. No WineHQ repository is added to
the host. The real WineHQ Staging launcher requires both `wine-staging-amd64` and
`wine-staging-i386:i386`, so the image installs both and verifies the package
set. The Debian `wine` and `wine64` baseline remains separate. This is a
runtime provisioning step, not proof that every Win32 application works. Binary package
installation does **not** include an editable Wine-Staging source checkout.

## Rebuild the image on your host

Run **on the Linux host, outside `kitt re shell linux`**, with Agent CLI
0.88.13 or newer. Prefer the CI-built published image:

```sh
kitt vm container pull --runtime docker --image toolbox --profile full
docker image inspect --format '{{.Id}}' kitt/linux-re:full-0.86.11
```

Or build the image locally from the checked-out Agent CLI source:

```sh
kitt vm container build --runtime docker --image toolbox --profile full
```

For the existing managed `kitt-re-full`, replace only its container while
retaining its bind-mounted `/workspace` and leaving Windows untouched:

```sh
kitt re lab upgrade-linux --approve
kitt re shell linux
wine --version
wine64 --version
wine-staging --version
cat /usr/share/kitt/re-wine-packages.lock
```

The command requires the new immutable local image to exist and pass a
networkless tool-version preflight; it does **not** pull or rebuild implicitly.
If the replacement fails, KITT attempts to restore the previous container by
its exact old image ID. It does not call the destructive `recreate` operation,
change the Windows guest or delete `/workspace`. Stop interactive sessions
and commit or backup any important workspace data beforehand. In case of
unexpected daemon failures, inspect Docker manually before rerunning.

## Working on Wine-Staging source

Running `wine-staging` installs the **runtime**, not the source code.
Wine-Staging is a patch collection layered on a matching upstream Wine
revision. For source development, obtain both official upstream Wine and
Wine-Staging repositories in a separate trusted, operator-approved developer
checkout, review and pin the revisions, then apply the patchset according to
the selected version's instructions. Do not mount Windows Oracle private
files/observations into the implementer's checkout. Keep distinct prefixes
when comparing engines:

```sh
WINEPREFIX=/workspace/prefix-debian wine --version
WINEPREFIX=/workspace/prefix-staging wine-staging --version
```

Wine prefixes are not safely interchangeable between arbitrary Wine versions.
Prefer disposable prefixes under `/tmp` for untrusted sample testing.
The container shares the host kernel: use a dedicated VM for unsafe third-party
samples.

## Validation and limits

The CI image workflow must actually build the Wine and full profiles and
check all three commands plus the signed package lock. Python tests verify
upgrade authorization, preflight ordering, rollback and preserving the lab
workspace. A green CI run is not a Windows Oracle differential qualification.
The existing Wine pilot continues to use the Debian native loader as a fixed
baseline; switching its reference engine requires separately reviewed tests.

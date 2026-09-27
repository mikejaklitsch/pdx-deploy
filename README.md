# pdx-deploy

Deploys a Paradox dev mod to its game-readable location. Development happens in a `.dev-mods/<Name> Development/` source tree that the game never loads; this tool copies the game directories to the deployed mod root that the game actually reads, taking the deploy name from `metadata.json` and stripping a trailing " (Dev)" suffix.

## Install

Python package with no dependencies. Install it editable with pipx so changes to the source take effect immediately:

```bash
pipx install -e .
```

## Usage

```bash
pdx-deploy                  # deploy the mod containing the current directory
pdx-deploy /path/to/mod     # deploy a specific mod
pdx-deploy --no-lint        # skip the lint gate
pdx-deploy --full           # wipe the deployed mod and copy everything
pdx-deploy --exclude PAT    # leave out files matching PAT (repeatable)
pdx-deploy --exclude-from F # leave out the patterns in F, one per line
```

The mod root is found automatically via its `.metadata/` directory. The deploy target is the parent of `.dev-mods/`, so a source tree at `mod/.dev-mods/My Mod Development/` deploys to `mod/My Mod/`.

Unless `--no-lint` is passed, pdx-lint runs first and a failing lint aborts the deploy.

## Incremental deploys

Only files that changed are copied. A file is recopied when either the source or the deployed copy differs, so edits made directly in the deployed mod are still overwritten. Files in the deployed mod that no longer exist in the source are removed. The deploy prints what it copied and removed, or "Up to date".

When the mod lives on a Windows drive (`/mnt/...`), the sync runs through `robocopy.exe /MIR` via WSL interop, which compares files natively on NTFS instead of through the slow drvfs layer. Elsewhere it falls back to a Python sync that keeps a manifest of file stats in `~/.cache/pdx-deploy/manifests/`.

`metadata.json` is written separately with " (Dev)" stripped from the name, and only when the result differs from what is deployed.

## Excluding files

`--exclude` takes a glob on the path relative to the mod root, such as `in_game/common/on_action/*`. A pattern that names a directory, such as `main_menu/setup`, covers everything under it. `--exclude-from` reads one pattern per line; blank lines and lines that start with `#` are skipped.

Excluded files are not copied, and their deployed copies are removed, so the game loads the mod as if those files did not exist. The dev tree does not change. The next deploy without the pattern copies them back. Use it to bisect a problem: deploy with half the mod excluded, test, and narrow down.

A deploy with excludes uses the Python sync, because robocopy cannot take a long list of excluded paths.

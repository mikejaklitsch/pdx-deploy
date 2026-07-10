# pdx-deploy

Deploys a Paradox dev mod to its game-readable location. Development happens in a `.dev-mods/<Name> Development/` source tree that the game never loads; this tool copies the game directories to the deployed mod root that the game actually reads, taking the deploy name from `metadata.json` and stripping a trailing " (Dev)" suffix.

## Install

Self-contained Python script, no dependencies. Symlink it onto your PATH:

```bash
ln -s "$(pwd)/pdx-deploy" ~/.local/bin/pdx-deploy
```

## Usage

```bash
pdx-deploy                  # deploy the mod containing the current directory
pdx-deploy /path/to/mod     # deploy a specific mod
pdx-deploy --no-lint        # skip the lint gate
```

The mod root is found automatically via its `.metadata/` directory. The deploy target is the parent of `.dev-mods/`, so a source tree at `mod/.dev-mods/My Mod Development/` deploys to `mod/My Mod/`.

Unless `--no-lint` is passed, pdx-lint runs first and a failing lint aborts the deploy.

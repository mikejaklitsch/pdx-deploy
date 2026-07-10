# pdx-deploy

Deploy a Paradox dev mod to its game-readable location.

Auto-detects the mod root via .metadata/, reads the deploy name from
metadata.json (stripping " (Dev)"), and copies game directories to the
deploy root (the parent of .dev-mods/).

Usage:
    pdx-deploy                  # deploy current directory
    pdx-deploy /path/to/mod     # deploy specific mod
    pdx-deploy --no-lint        # skip lint checks

## Install

Self-contained Python 3.9+ script, no dependencies. Symlink or copy it onto your PATH:

```bash
ln -s "$(pwd)/pdx-deploy" ~/.local/bin/pdx-deploy
```

# homebrew-tap

Homebrew formulae for Gradipoo projects.

`rn` installs [tui-renamer](https://github.com/Gradipoo/tui-renamer) with the HEIC extra.

The sdist url is a placeholder until the first PyPI release. `brew install` cannot fetch it before that. After the release, run `packaging/update_homebrew_formula.py` in the tui-renamer repo (or `brew update-python-resources` on this formula) and update the tap.

```bash
brew install gradipoo/tap/rn
```

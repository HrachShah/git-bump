# git-bump

A tiny CLI to bump a [SemVer 2.0.0](https://semver.org/) version inside a project
file (`package.json`, `pyproject.toml`, or a plain text file containing a
`__version__ = "..."` line) and create a git commit + annotated tag for the new
version. Useful as a pre-script for releases — pair it with `git push --follow-tags`.

## Install

```bash
pip install git-bump
# or, with pipx (recommended for one-off CLIs)
pipx install git-bump
# or, copy `git_bump.py` somewhere on your $PATH
```

## Usage

```bash
git bump patch               # 0.1.2 -> 0.1.3
git bump minor               # 0.1.3 -> 0.2.0
git bump major               # 0.2.0 -> 1.0.0
git bump --set 1.0.0-rc1     # set a specific version
git bump --file package.json # choose the file (default: auto-detect)
git bump --no-commit         # only write the new version, skip git commit/tag
git bump --dry-run           # print the actions without performing them
git bump --help
```

`git-bump` will:

1. Read the current version from the first file it can find in this order:
   `package.json`, `pyproject.toml`, `__init__.py` with `__version__`, or a
   `VERSION` plain text file.
2. Compute the next version according to the bump level.
3. Write the new version back to the same file (preserving the rest of the
   file byte-for-byte, including JSON formatting and TOML section order).
4. If `--no-commit` is **not** given, run `git add` on the file, create a
   commit titled `release: v<new_version>`, and create an annotated tag
   `v<new_version>`. The commit and tag are signed with whatever `user.name` /
   `user.email` git is configured to use.
5. Print the diff hunks it made to the file on stderr so you can spot-check
   before pushing.

## Why

The `npm version` and `bumpversion` tools are good, but they're tied to
JavaScript / Python and either rewrite the whole file (`npm version` reformats
`package.json` with `JSON.stringify`) or require a config file
(`bumpversion`). `git-bump` keeps the file formatting intact (it patches
`package.json` and `pyproject.toml` in place with a regex on the version
field, not a re-serialize) and has zero config.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0    | Bump succeeded (commit + tag created, or `--no-commit`) |
| 1    | Pre-flight error: not a git repo, no supported file found, version already exists, etc. |
| 2    | Invalid CLI argument |
| 3    | Git command failed (conflict, dirty tree, etc.) |

## Testing

```bash
python3 -m unittest discover -s tests
```

## License

MIT

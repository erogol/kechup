# Contributing to kechup

Thank you for your help. Keep changes small and focused.

## Platform

kechup runs on Linux and macOS only. It uses `fcntl` file locks, so Windows is not supported.

## Dev install

You need Python 3.12 or newer and git.

```sh
git clone https://github.com/erogol/kechup
cd kechup
python3 -m venv .venv
. .venv/bin/activate
pip install -e . pytest
```

## Run the tests

```sh
python3 -m pytest -q
```

The tests use real temporary git repositories. Set a git user name and email before you run them:

```sh
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## Code style

- Use only the Python standard library. Do not add dependencies.
- Use type hints. Keep functions short.
- Handle errors explicitly. Do not hide a failure.
- Add a test for each change.

## Commits and pull requests

- One topic per commit. Use a short prefix: `fix:`, `feat:`, `docs:`, `test:`, `build:`, `ci:`.
- Make sure the tests pass before you open a pull request.
- Read "What kechup does not do" in the README before you propose a new feature.

## AI-assisted contributions

You can use AI tools. You are responsible for every line you submit: read it, test it, and explain it in the pull request.

## Security

Do not report security problems in public issues. See [SECURITY.md](SECURITY.md).

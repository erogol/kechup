"""Secret-like file names (spec section 8): common credential files are
refused, normal source files and env templates are not."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import run_kechup

from kechup.safety import is_secret_like

DOT = "."

REFUSED = [
    DOT + "env",
    DOT + "env.local",
    DOT + "env.production",
    "prod" + DOT + "env",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    DOT + "npmrc",
    DOT + "netrc",
    DOT + "pypirc",
    DOT + "pgpass",
    DOT + "git-credentials",
    "oauth.json",
    "token.json",
    "token.txt",
    "credentials.json",
    "service-account.json",
    "gcp_service_account.json",
    "kubeconfig",
    "kubeconfig.yaml",
    "server.key",
    "server.pem",
    "cert.p12",
    "cert.pfx",
    "keystore.jks",
    "release.keystore",
    "vault.kdbx",
    "secrets/notes.txt",
]

ALLOWED = [
    DOT + "env.example",
    DOT + "env.sample",
    DOT + "env.template",
    "environment.py",
    "tokenizer.py",
    "README.md",
    "config.json",
    "keys.md",
    "src/identity.py",
]


@pytest.mark.parametrize("name", REFUSED)
def test_secret_like_names_refused(name: str) -> None:
    assert is_secret_like(Path(name)), name


@pytest.mark.parametrize("name", ALLOWED)
def test_normal_names_allowed(name: str) -> None:
    assert not is_secret_like(Path(name)), name


def test_commit_refuses_env_local(new_project: Path) -> None:
    name = DOT + "env.local"
    (new_project / name).write_text("X=1\n")
    result = run_kechup(["commit", name, "-m", "oops"], cwd=new_project, agent="x")
    assert result.exit_code == 3
    assert result.json["error"]["code"] == "secret_like"

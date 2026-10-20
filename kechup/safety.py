"""Safety checks: file size, secret-like names, path containment (spec section 8)."""

from __future__ import annotations

from pathlib import Path

MAX_FILE_BYTES = 5 * 1024 * 1024

_SECRET_NAME_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".env")
_SECRET_NAME_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_SECRET_NAME_EXACT = (
    ".env",
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".pgpass",
    ".git-credentials",
    "oauth.json",
    "token.json",
    "token.txt",
)
_SECRET_NAME_SUBSTRINGS = (
    "secret",
    "credential",
    "kubeconfig",
    "service-account",
    "service_account",
)
_SECRET_DIR_NAMES = ("secrets",)
# `.env.<x>` files hold real values, except these committed templates.
_ENV_TEMPLATE_NAMES = (".env.example", ".env.sample", ".env.template")


def is_secret_like(path: Path) -> bool:
    """A path is "key-like" (section 8), checked case-insensitively."""
    name = path.name.lower()
    if name.endswith(_SECRET_NAME_SUFFIXES):
        return True
    if any(name.startswith(prefix) for prefix in _SECRET_NAME_PREFIXES):
        return True
    if name in _SECRET_NAME_EXACT:
        return True
    if name.startswith(".env.") and name not in _ENV_TEMPLATE_NAMES:
        return True
    if any(token in name for token in _SECRET_NAME_SUBSTRINGS):
        return True
    for part in path.parts[:-1]:
        if part.lower() in _SECRET_DIR_NAMES:
            return True
    return False


def is_too_large(path: Path, max_bytes: int = MAX_FILE_BYTES) -> bool:
    return path.stat().st_size > max_bytes


def is_in_nested_repo(root: Path, rel: str) -> bool:
    """True when `rel` sits inside a subfolder that has its own `.git`
    (shared by the snapshot scan and the commit path expansion, FIX_SPEC_F3
    R2/R9)."""
    path = root / rel
    for parent in path.parents:
        if parent == root:
            break
        if (parent / ".git").exists():
            return True
    return False


def resolve_within(root: Path, candidate: Path) -> Path | None:
    """Resolve `candidate` (absolute or relative to root) and require it stay
    inside `root` (resolved). Returns the resolved path, or None if it escapes.
    Uses Path.relative_to, never a string prefix (section 8)."""
    root_resolved = root.resolve()
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        target_resolved = target.resolve()
    except OSError:
        return None
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return target_resolved

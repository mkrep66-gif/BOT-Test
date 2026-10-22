"""The private settings file (secrets/secrets.yaml).

Holds wallet seeds, the dashboard password hash, alert tokens. Rules:
  * the folder is mode 700 and the file mode 600 (only your user can read it)
  * we refuse to start if anyone else can read it
  * values are never printed - use describe() for a safe summary
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets as pysecrets
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config import SECRETS_DIR

SECRETS_FILE = "secrets.yaml"


class SecretsError(RuntimeError):
    pass


def secrets_path() -> Path:
    return SECRETS_DIR / SECRETS_FILE


def _check_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    st = path.stat()
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretsError(
            f"{path} can be read by other users. Fix it with:  chmod 600 {path}"
        )
    dst = path.parent.stat()
    if dst.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretsError(
            f"{path.parent} is open to other users. Fix it with:  chmod 700 {path.parent}"
        )


def load() -> Dict[str, Any]:
    p = secrets_path()
    if not p.exists():
        return {}
    _check_permissions(p)
    return yaml.safe_load(p.read_text()) or {}


def save(data: Dict[str, Any]) -> None:
    p = secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# PRIVATE. Never share, paste, screenshot or commit this file.\n")
        yaml.safe_dump(data, f, sort_keys=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


# ---- wallets --------------------------------------------------------------

def wallets(network: str) -> List[Dict[str, str]]:
    """List of {name, address, seed} for the network."""
    return list(load().get("wallets", {}).get(network, []))


def add_wallet(network: str, name: str, address: str, seed: str) -> None:
    data = load()
    ws = data.setdefault("wallets", {}).setdefault(network, [])
    if any(w["name"] == name for w in ws):
        raise SecretsError(f"wallet {name} already exists on {network}")
    ws.append({"name": name, "address": address, "seed": seed})
    save(data)


def all_seeds() -> List[str]:
    out = []
    for ws in load().get("wallets", {}).values():
        out.extend(w["seed"] for w in ws if w.get("seed"))
    return out


def get(key: str, default: Any = None) -> Any:
    cur: Any = load()
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_value(key: str, value: Any) -> None:
    data = load()
    cur = data
    parts = key.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value
    save(data)


def describe() -> str:
    """Safe summary for humans: names and addresses only, never secrets."""
    data = load()
    lines = [f"Private settings file: {secrets_path()}"]
    for net, ws in (data.get("wallets") or {}).items():
        lines.append(f"  {net} wallets:")
        for w in ws:
            lines.append(f"    - {w['name']}: {w['address']}  (secret key stored: yes)")
    lines.append(f"  dashboard password set: {'yes' if get('dashboard.password_hash') else 'no'}")
    lines.append(f"  telegram alerts set up: {'yes' if get('alerts.telegram.bot_token') else 'no'}")
    lines.append(f"  email alerts set up: {'yes' if get('alerts.email.smtp_host') else 'no'}")
    return "\n".join(lines)


# ---- dashboard password ---------------------------------------------------

def hash_password(password: str) -> str:
    salt = pysecrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, dk_b64 = stored.split("$")
    salt = base64.b64decode(salt_b64)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def session_key() -> bytes:
    k = get("dashboard.session_key")
    if not k:
        k = pysecrets.token_hex(32)
        set_value("dashboard.session_key", k)
    return bytes.fromhex(k)


# ---- encrypted backup -----------------------------------------------------

def encrypt_backup(passphrase: str, out_path: Path) -> None:
    """Write an encrypted copy of the secrets file (AES-256-GCM, scrypt key)."""
    from Crypto.Cipher import AES

    plain = secrets_path().read_bytes()
    salt = pysecrets.token_bytes(16)
    key = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=2**26)
    cipher = AES.new(key, AES.MODE_GCM)
    ct, tag = cipher.encrypt_and_digest(plain)
    blob = b"XARB1" + salt + cipher.nonce + tag + ct
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)


def decrypt_backup(passphrase: str, in_path: Path) -> bytes:
    from Crypto.Cipher import AES

    blob = in_path.read_bytes()
    if not blob.startswith(b"XARB1"):
        raise SecretsError("not an xrplarb backup file")
    salt, nonce, tag, ct = blob[5:21], blob[21:37], blob[37:53], blob[53:]
    key = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=2**26)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    try:
        return cipher.decrypt_and_verify(ct, tag)
    except ValueError as e:
        raise SecretsError("wrong passphrase or damaged backup") from e

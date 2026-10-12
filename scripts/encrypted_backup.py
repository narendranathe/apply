#!/usr/bin/env python3
"""Authenticated migration archives. Keys and restored plaintext stay outside Git.

age is preferred when installed and an identity/recipient is supplied. The
fallback is AES-256-GCM from cryptography, with a fresh 96-bit nonce per archive.
No key material is written to stdout or archive metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile

MAGIC = b"APPLY-MIGRATION-AES256GCM-v1\x00"
MANIFEST_SCHEMA = "apply-encrypted-backup/v1"
DENIED_NAMES = ("cookie", "credential", "password", "token", "secret", "browser", "keychain", ".env", "login data")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name


def outside_git(path: Path) -> bool:
    return not any((parent / ".git").exists() for parent in [path.resolve().parent, *path.resolve().parents])


def private_key(path: Path, *, create: bool = False) -> bytes:
    path = path.resolve()
    if not outside_git(path):
        raise ValueError("Encryption keys must be outside every Git checkout")
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(os.urandom(32))
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Key file must have mode 0600 or stricter")
    data = path.read_bytes()
    if len(data) != 32:
        raise ValueError("AES-256-GCM key must be 32 bytes")
    return data


def sqlite_snapshot(source: Path, destination: Path) -> None:
    """SQLite backup API includes committed WAL data without mutating the source."""
    source = source.resolve()
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite snapshot integrity check failed")
    destination.chmod(0o600)


def build_archive(files: dict[str, Path]) -> tuple[bytes, dict]:
    entries = []
    contents = {}
    for name, path in sorted(files.items()):
        if not safe_name(name) or any(term in name.lower() for term in DENIED_NAMES):
            raise ValueError("Unsafe or credential-class archive member")
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Backup sources must be regular files")
        data = path.read_bytes()
        contents[name] = data
        entries.append({"name": name, "bytes": len(data), "sha256": sha(data)})
    manifest = {"schema": MANIFEST_SCHEMA, "files": entries}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in [("MANIFEST.json", json.dumps(manifest, sort_keys=True).encode()),
                           *[("files/" + n, d) for n, d in contents.items()]]:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue(), manifest


def encrypt_bytes(data: bytes, *, key_path: Path | None = None, recipient: str | None = None) -> tuple[bytes, str]:
    if recipient:
        if not shutil.which("age"):
            raise RuntimeError("age is unavailable; install it or explicitly select AES-GCM")
        result = subprocess.run(["age", "--encrypt", "--recipient", recipient], input=data, capture_output=True)
        if result.returncode:
            raise RuntimeError("age encryption failed")
        return result.stdout, "age"
    if key_path is None:
        raise ValueError("An external key or age recipient is required")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(private_key(key_path)).encrypt(nonce, data, MAGIC), "AES-256-GCM"


def decrypt_bytes(data: bytes, *, key_path: Path | None = None, identity: Path | None = None) -> bytes:
    if data.startswith(MAGIC):
        if key_path is None:
            raise ValueError("An external AES key is required")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, ciphertext = data[len(MAGIC):len(MAGIC) + 12], data[len(MAGIC) + 12:]
        if len(nonce) != 12:
            raise ValueError("Incomplete authenticated archive")
        return AESGCM(private_key(key_path)).decrypt(nonce, ciphertext, MAGIC)
    if identity is None or not shutil.which("age"):
        raise ValueError("An external age identity and age binary are required")
    if not outside_git(identity) or stat.S_IMODE(identity.stat().st_mode) & 0o077:
        raise ValueError("age identity must be private and outside Git")
    result = subprocess.run(["age", "--decrypt", "--identity", str(identity)], input=data, capture_output=True)
    if result.returncode:
        raise ValueError("age authentication/decryption failed")
    return result.stdout


def restore_archive(archive_path: Path, destination: Path, *, key_path: Path | None = None,
                    identity: Path | None = None) -> dict:
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore destination must be empty")
    if not outside_git(destination) and ".migration-private" not in destination.parts:
        raise ValueError("Restore plaintext must be outside Git or in .migration-private")
    data = decrypt_bytes(archive_path.read_bytes(), key_path=key_path, identity=identity)
    # Authenticate, validate all members/hashes, then write anything.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if any(not m.isfile() or not safe_name(m.name) for m in members):
            raise ValueError("Unsafe archive member")
        if len({m.name for m in members}) != len(members):
            raise ValueError("Duplicate archive member")
        manifest = json.loads(archive.extractfile("MANIFEST.json").read())
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise ValueError("Unsupported archive manifest")
        expected = {"files/" + row["name"]: row for row in manifest["files"]}
        if set(expected) | {"MANIFEST.json"} != {m.name for m in members}:
            raise ValueError("Archive and manifest members differ")
        contents = {}
        for name, item in expected.items():
            if not safe_name(item["name"]):
                raise ValueError("Unsafe manifest path")
            body = archive.extractfile(name).read()
            if len(body) != item["bytes"] or sha(body) != item["sha256"]:
                raise ValueError("Archive integrity mismatch")
            contents[item["name"]] = body
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    for name, body in contents.items():
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
    return {"restored_files": len(contents), "hashes_verified": True, "manifest": manifest}


def create_backup(files: dict[str, Path], output: Path, *, key_path: Path | None = None,
                  recipient: str | None = None, identity: Path | None = None,
                  private_destination_verified: bool = False) -> dict:
    if not private_destination_verified:
        raise ValueError("Private destination must be verified before archive creation")
    if output.exists():
        raise ValueError("Never overwrite a historical archive")
    plaintext, manifest = build_archive(files)
    encrypted, algorithm = encrypt_bytes(plaintext, key_path=key_path, recipient=recipient)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encrypted)
    try:
        with tempfile.TemporaryDirectory(prefix="apply-restore-") as tmp:
            restore = restore_archive(output, Path(tmp) / "restored", key_path=key_path, identity=identity)
            for item in manifest["files"]:
                if sha((Path(tmp) / "restored" / item["name"]).read_bytes()) != item["sha256"]:
                    raise ValueError("Disposable restore hash comparison failed")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {"schema": MANIFEST_SCHEMA, "algorithm": algorithm, "archive_sha256": sha(encrypted),
            "archive_bytes": len(encrypted), "files": manifest["files"],
            "restore_verified": restore["hashes_verified"], "key_in_archive": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    key = sub.add_parser("keygen")
    key.add_argument("--key", type=Path, required=True)
    create = sub.add_parser("create")
    create.add_argument("--file", action="append", required=True, help="archive-name=source-path")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--key", type=Path)
    create.add_argument("--recipient")
    create.add_argument("--identity", type=Path)
    create.add_argument("--private-destination-verified", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--key", type=Path)
    restore.add_argument("--identity", type=Path)
    args = p.parse_args()
    if args.command == "keygen":
        private_key(args.key, create=True)
        print(json.dumps({"created": True, "mode": "0600", "key_material_printed": False}))
    elif args.command == "create":
        files = {}
        for value in args.file:
            name, sep, source = value.partition("=")
            if not sep or name in files:
                p.error("Each --file must use a unique archive-name=source-path")
            files[name] = Path(source)
        print(json.dumps(create_backup(files, args.output, key_path=args.key, recipient=args.recipient,
                                       identity=args.identity, private_destination_verified=args.private_destination_verified)))
    else:
        result = restore_archive(args.archive, args.destination, key_path=args.key, identity=args.identity)
        print(json.dumps({k: v for k, v in result.items() if k != "manifest"}))


if __name__ == "__main__":
    main()

from __future__ import annotations

import re
import shutil
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from panel.models import Client


class FileManagerError(ValueError):
    pass


SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = SAFE_SEGMENT_RE.sub("-", (value or "").strip().lower()).strip(".-")
    return cleaned or fallback


def client_root(client_id: int) -> Path:
    client = Client.query.get(client_id)
    if client is not None and client.user is not None:
        username = _safe_segment(client.user.username, f"client-{client_id}")
        root = Path(current_app.config.get("CLIENT_HOME_ROOT", current_app.config["STORAGE_ROOT"])) / username
    else:
        root = Path(current_app.config.get("CLIENT_HOME_ROOT", current_app.config["STORAGE_ROOT"])) / f"client_{client_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_join(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    if root not in target.parents and target != root:
        raise FileManagerError("Niedozwolona ścieżka.")
    return target


def list_directory(root: Path, relative_path: str = "") -> list[dict]:
    target = safe_join(root, relative_path)
    items = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        items.append(
            {
                "name": item.name,
                "path": str(item.relative_to(root)),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size,
            }
        )
    return items


def save_upload(root: Path, relative_dir: str, storage_file) -> Path:
    directory = safe_join(root, relative_dir)
    directory.mkdir(parents=True, exist_ok=True)

    filename = secure_filename(storage_file.filename or "")
    if not filename:
        raise FileManagerError("Niedozwolona nazwa pliku.")

    target = safe_join(root, str(Path(relative_dir) / filename))
    storage_file.save(target)
    return target


def write_text_file(root: Path, relative_path: str, content: str) -> Path:
    target = safe_join(root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def create_folder(root: Path, relative_path: str) -> Path:
    target = safe_join(root, relative_path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def delete_path(root: Path, relative_path: str) -> None:
    target = safe_join(root, relative_path)
    if target == root:
        raise FileManagerError("Nie można usunąć katalogu głównego klienta.")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def move_path(root: Path, source: str, destination: str) -> Path:
    source_path = safe_join(root, source)
    destination_path = safe_join(root, destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(source_path), str(destination_path)))


def copy_path(root: Path, source: str, destination: str) -> Path:
    source_path = safe_join(root, source)
    destination_path = safe_join(root, destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.is_dir():
        shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
    else:
        shutil.copy2(source_path, destination_path)
    return destination_path

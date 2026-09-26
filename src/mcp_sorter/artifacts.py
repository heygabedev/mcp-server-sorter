import os
from pathlib import Path
from uuid import uuid4

from filelock import FileLock


def replace_text(path: Path, content: str) -> None:
    """Atomically replace a derived view after its complete contents reach disk."""
    temporary = path.parent / f".{uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        with FileLock(path.with_name(f".{path.name}.lock"), timeout=5):
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_once(path: Path, content: str) -> None:
    """Publish a complete immutable file without replacing an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

import os
from pathlib import Path
from uuid import uuid4


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

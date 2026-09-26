from time import time
from typing import Literal

from sqlalchemy import Engine, text


def record_event(engine: Engine, kind: Literal["fallback", "job"], code: str) -> None:
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO events(at,kind,code) VALUES (:at,:kind,:code)"),
            {"at": time(), "kind": kind, "code": code},
        )
        db.execute(
            text(
                "DELETE FROM events WHERE id NOT IN "
                "(SELECT id FROM events ORDER BY id DESC LIMIT 1000)"
            )
        )


def recent_events(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                text("SELECT * FROM events ORDER BY id DESC LIMIT 100")
            ).mappings()
        ]

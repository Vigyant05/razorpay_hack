"""Append-only audit ledger (invariant #3).

One SQLite table, one row per run-step. This module exposes only `append` and
`read` — no update, no delete — so immutability is enforced by the API surface,
not by convention.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import get_settings
from .domain import LedgerStep


class LedgerEntry(SQLModel, table=True):
    __tablename__ = "ledger"

    id: int | None = Field(default=None, primary_key=True)
    episode_id: str = Field(index=True)
    step: LedgerStep
    payload: str  # a domain model, model_dump_json()'d
    signature: str | None = None  # set only for mandate rows
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _engine(db_path: str | None = None):
    engine = create_engine(f"sqlite:///{db_path or get_settings().db_path}")
    SQLModel.metadata.create_all(engine)
    return engine


def append(
    episode_id: str,
    step: LedgerStep,
    payload: BaseModel,
    signature: str | None = None,
    db_path: str | None = None,
) -> LedgerEntry:
    """Write one immutable record. Returns the persisted entry (with its id)."""
    entry = LedgerEntry(
        episode_id=episode_id,
        step=step,
        payload=payload.model_dump_json(),
        signature=signature,
    )
    with Session(_engine(db_path)) as session:
        session.add(entry)
        session.commit()
        session.refresh(entry)
    return entry


def read(episode_id: str, db_path: str | None = None) -> list[LedgerEntry]:
    """All entries for an episode, oldest first."""
    with Session(_engine(db_path)) as session:
        return list(
            session.exec(
                select(LedgerEntry)
                .where(LedgerEntry.episode_id == episode_id)
                .order_by(LedgerEntry.id)
            )
        )

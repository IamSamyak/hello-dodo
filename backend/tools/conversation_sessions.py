
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hello_dodo_sessions.sqlite3"

router = APIRouter(tags=["conversation-sessions"])

TERMINAL_STATUSES = {"completed", "failed"}
VALID_STATUSES = {
    "running",
    "needs_clarification",
    "waiting_for_tool",
    "completed",
    "failed",
}
VALID_EVENTS = {
    "thinking",
    "needs_clarification",
    "tool_started",
    "tool_completed",
    "answer_ready",
    "error",
}


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                original_message TEXT NOT NULL,
                pending_question TEXT,
                pending_reply TEXT,
                result TEXT,
                context_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(session_id)
                    REFERENCES sessions(session_id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_session_events_session_id_id
            ON session_events(session_id, id)
            """
        )


def create_session(message: str) -> dict[str, Any]:
    message = message.strip()

    if not message:
        raise ValueError("Message cannot be empty.")

    now = time.time()
    session_id = str(uuid.uuid4())

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions (
                session_id,
                status,
                original_message,
                context_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                "running",
                message,
                "{}",
                now,
                now,
            ),
        )

    append_event(
        session_id,
        "thinking",
        {"message": "Request received."},
    )

    return get_session(session_id)


def get_session(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    if row is None:
        raise KeyError("Session not found.")

    session = dict(row)
    session["context"] = json.loads(
        session.pop("context_json") or "{}"
    )

    return session


def update_session(
    session_id: str,
    *,
    status: str | None = None,
    pending_question: str | None = None,
    pending_reply: str | None = None,
    result: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError("Invalid session status.")

    updates: dict[str, Any] = {
        "updated_at": time.time(),
    }

    if status is not None:
        updates["status"] = status

    if pending_question is not None:
        updates["pending_question"] = pending_question

    if pending_reply is not None:
        updates["pending_reply"] = pending_reply

    if result is not None:
        updates["result"] = result

    if context is not None:
        updates["context_json"] = json.dumps(
            context,
            ensure_ascii=False,
        )

    assignments = ", ".join(
        f"{column} = ?" for column in updates
    )
    values = list(updates.values()) + [session_id]

    with _connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE sessions
            SET {assignments}
            WHERE session_id = ?
            """,
            values,
        )

        if cursor.rowcount == 0:
            raise KeyError("Session not found.")


def append_event(
    session_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> int:
    if event_type not in VALID_EVENTS:
        raise ValueError("Unsupported event type.")

    now = time.time()
    payload = json.dumps(
        data or {},
        ensure_ascii=False,
    )

    with _connect() as connection:
        exists = connection.execute(
            """
            SELECT 1
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

        if exists is None:
            raise KeyError("Session not found.")

        cursor = connection.execute(
            """
            INSERT INTO session_events (
                session_id,
                event_type,
                data_json,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                session_id,
                event_type,
                payload,
                now,
            ),
        )

        connection.execute(
            """
            UPDATE sessions
            SET updated_at = ?
            WHERE session_id = ?
            """,
            (now, session_id),
        )

        return int(cursor.lastrowid)


def read_events(
    session_id: str,
    after_id: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, event_type, data_json, created_at
            FROM session_events
            WHERE session_id = ? AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (
                session_id,
                after_id,
                limit,
            ),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "type": row["event_type"],
            "data": json.loads(row["data_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


class CreateSessionRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


class ClarificationReplyRequest(BaseModel):
    reply: str = Field(min_length=1, max_length=10000)


@router.post("/sessions")
def create_session_endpoint(
    request: CreateSessionRequest,
):
    try:
        return create_session(request.message)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/sessions/{session_id}")
def get_session_endpoint(
    session_id: str,
):
    try:
        return get_session(session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@router.post("/sessions/{session_id}/reply")
def submit_clarification_reply(
    session_id: str,
    request: ClarificationReplyRequest,
):
    reply = request.reply.strip()

    if not reply:
        raise HTTPException(
            status_code=400,
            detail="Reply cannot be empty.",
        )

    try:
        session = get_session(session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    if session["status"] != "needs_clarification":
        raise HTTPException(
            status_code=409,
            detail=(
                "This session is not waiting "
                "for clarification."
            ),
        )

    update_session(
        session_id,
        status="running",
        pending_reply=reply,
    )

    append_event(
        session_id,
        "thinking",
        {"message": "Clarification received."},
    )

    return {
        "session_id": session_id,
        "status": "running",
        "message": (
            "Reply saved. Orchestration must resume "
            "this existing session."
        ),
    }


@router.get("/sessions/{session_id}/events")
async def stream_session_events(
    session_id: str,
    after_id: int = Query(default=0, ge=0),
):
    try:
        get_session(session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    async def generate():
        nonlocal after_id

        last_heartbeat = time.monotonic()

        yield "retry: 2000\n\n"

        while True:
            events = await asyncio.to_thread(
                read_events,
                session_id,
                after_id,
            )

            for event in events:
                after_id = event["id"]

                payload = json.dumps(
                    event["data"],
                    ensure_ascii=False,
                )

                yield (
                    f"id: {event['id']}\n"
                    f"event: {event['type']}\n"
                    f"data: {payload}\n\n"
                )

            try:
                session = await asyncio.to_thread(
                    get_session,
                    session_id,
                )
            except KeyError:
                return

            if (
                session["status"] in TERMINAL_STATUSES
                and not events
            ):
                return

            if time.monotonic() - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = time.monotonic()

            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


initialize()
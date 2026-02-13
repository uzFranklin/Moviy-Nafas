from __future__ import annotations

import sqlite3
from typing import Optional, Literal, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

DB_PATH = "db.db"

app = FastAPI(title="TrashMap API", version="1.0")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_tid INTEGER,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            note TEXT,
            status TEXT CHECK(status IN ('pending','approved','rejected')) NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    conn.commit()
    conn.close()


ensure_schema()


# ---------- Models ----------
class TrashPointCreate(BaseModel):
    user_tid: Optional[int] = None
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    note: str = ""
    init_data: Optional[str] = None  # на будущее (проверка подписи Telegram)


class TrashPointOut(BaseModel):
    id: int
    user_tid: Optional[int]
    lat: float
    lon: float
    note: str
    status: Literal["pending", "approved", "rejected"]
    created_at: str


class TrashPointsList(BaseModel):
    items: List[TrashPointOut]


class StatusUpdate(BaseModel):
    status: Literal["pending", "approved", "rejected"]


# ---------- CORS (минимально) ----------
# Если хочешь безопасно: поставь конкретный домен вместо "*"
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # лучше заменить на ["https://YOUR_WEBAPP_DOMAIN"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Routes ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/trashpoints")
def create_trashpoint(p: TrashPointCreate):
    # Тут можно позже проверить p.init_data (Telegram signature), если захочешь строго.
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO trash_points (user_tid, lat, lon, note, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (p.user_tid, p.lat, p.lon, p.note.strip()),
    )
    conn.commit()
    new_id = cur.lastrowid
    row = conn.execute("SELECT * FROM trash_points WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return TrashPointOut(**dict(row))


@app.get("/trashpoints", response_model=TrashPointsList)
def list_trashpoints(
    status: Optional[Literal["pending", "approved", "rejected"]] = Query(default="approved"),
    limit: int = Query(default=500, ge=1, le=2000),
):
    conn = db()
    if status:
        rows = conn.execute(
            "SELECT * FROM trash_points WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trash_points ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return TrashPointsList(items=[TrashPointOut(**dict(r)) for r in rows])


@app.patch("/trashpoints/{point_id}", response_model=TrashPointOut)
def update_status(point_id: int, body: StatusUpdate):
    conn = db()
    row = conn.execute("SELECT * FROM trash_points WHERE id=?", (point_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Not found")

    conn.execute("UPDATE trash_points SET status=? WHERE id=?", (body.status, point_id))
    conn.commit()
    row2 = conn.execute("SELECT * FROM trash_points WHERE id=?", (point_id,)).fetchone()
    conn.close()
    return TrashPointOut(**dict(row2))
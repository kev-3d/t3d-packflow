"""Session info for the frontend and user administration (admins only)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import (
    ClerkApiError, User, is_configured, is_valid_email, normalize_email, publishable_key,
    require_admin, require_user, send_invitation,
)
from .database import IntegrityError, connect, now_iso

router = APIRouter()

USER_COLUMNS = "id, email, full_name, role, is_active, clerk_user_id IS NOT NULL AS linked, created_at, last_login_at"


class NewUserPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(default="", max_length=200)
    role: Literal["admin", "user"] = "user"
    send_invitation: bool = True


class UserUpdatePayload(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


def _public_user(row: Any) -> dict[str, Any]:
    user = dict(row)
    user["is_active"] = bool(user["is_active"])
    user["linked"] = bool(user["linked"])
    return user


@router.get("/api/auth/config")
def auth_config() -> dict[str, Any]:
    """Public: the frontend needs the publishable key before anyone signs in."""
    return {"configured": is_configured(), "publishable_key": publishable_key() if is_configured() else ""}


@router.get("/api/auth/me")
def me(user: User = Depends(require_user)) -> dict[str, Any]:
    with connect() as db:
        db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user.id))
    return user.to_dict()


@router.get("/api/admin/users")
def list_users(_: User = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY lower(email)").fetchall()
    return [_public_user(row) for row in rows]


@router.post("/api/admin/users", status_code=201)
def create_user(payload: NewUserPayload, _: User = Depends(require_admin)) -> dict[str, Any]:
    email = normalize_email(payload.email)
    if not is_valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    timestamp = now_iso()
    try:
        with connect() as db:
            user_id = db.insert_returning_id(
                """INSERT INTO users (email, full_name, role, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?) RETURNING id""",
                (email, payload.full_name.strip(), payload.role, timestamp, timestamp),
            )
            row = db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    except IntegrityError as error:
        raise HTTPException(409, f"{email} already has access.") from error
    result: dict[str, Any] = {"user": _public_user(row), "invitation": "skipped"}
    if payload.send_invitation:
        result.update(_invite(email))
    return result


@router.post("/api/admin/users/{user_id}/invitation")
def resend_invitation(user_id: int, _: User = Depends(require_admin)) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "User not found.")
    return _invite(row["email"])


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, payload: UserUpdatePayload, actor: User = Depends(require_admin)) -> dict[str, Any]:
    with connect() as db:
        target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(404, "User not found.")
        loses_admin = target["role"] == "admin" and bool(target["is_active"]) and (
            (payload.role is not None and payload.role != "admin") or payload.is_active is False
        )
        if loses_admin and target["id"] == actor.id:
            raise HTTPException(409, "You cannot remove your own administrator access.")
        if loses_admin:
            others = db.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?", (user_id,)
            ).fetchone()[0]
            if others == 0:
                raise HTTPException(409, "PackFlow needs at least one active administrator.")
        updates: dict[str, Any] = {}
        if payload.full_name is not None:
            updates["full_name"] = payload.full_name.strip()
        if payload.role is not None:
            updates["role"] = payload.role
        if payload.is_active is not None:
            updates["is_active"] = 1 if payload.is_active else 0
        if updates:
            updates["updated_at"] = now_iso()
            assignments = ", ".join(f"{column} = ?" for column in updates)
            db.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*updates.values(), user_id))
        row = db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row)


def _invite(email: str) -> dict[str, Any]:
    try:
        send_invitation(email)
    except ClerkApiError as error:
        return {"invitation": "failed", "invitation_error": str(error)}
    return {"invitation": "sent"}

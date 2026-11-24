"""API for special client requirements (docs, courier, pricing and comments the packer must follow)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .database import Database, IntegrityError, connect, now_iso
from .requirements import (
    REQUIREMENT_TEXT_FIELDS, Prepared, best_match, company_name_variants, name_key,
    parse_requirements_excel, prepare,
)

router = APIRouter()

# Fields shown to the person packing the order. Invoice e-mail and payment method are billing data only.
NOTE_FIELDS = ("docs_required", "pricing_requirements", "courier", "comments")
_TEXT_COLUMNS = ", ".join(REQUIREMENT_TEXT_FIELDS)
_TEXT_ASSIGNMENTS = ", ".join(f"{field}=?" for field in REQUIREMENT_TEXT_FIELDS)


def _aliases_list(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _requirement_names(row: dict[str, Any], linked_customer: str | None) -> list[str]:
    names = company_name_variants(row["company"], _aliases_list(row["aliases"]))
    if linked_customer:
        names.append(linked_customer)
    return names


def _load_requirements(db: Database) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT r.*, c.customer AS client_name
           FROM client_requirements r LEFT JOIN clients c ON c.id = r.client_id
           ORDER BY lower(r.company)"""
    ).fetchall()
    return [dict(row) for row in rows]


def requirements_note_for(db: Database, client_name: str | None) -> dict[str, Any] | None:
    """Packing note for an order's customer: exact match on name, alias or linked client, else a suggestion."""
    if not client_name or not name_key(client_name):
        return None
    candidates = [
        (row["id"], prepare(name))
        for row in _load_requirements(db)
        for name in _requirement_names(row, row["client_name"])
    ]
    exact, suggestion = best_match([prepare(client_name)], candidates)
    if exact:
        requirement_id, match, score = exact[0], "exact", 1.0
    elif suggestion:
        (requirement_id, score), match = suggestion, "suggested"
    else:
        return None
    row = db.execute("SELECT * FROM client_requirements WHERE id = ?", (requirement_id,)).fetchone()
    # A billing-only row carries nothing for the person packing, so it must not raise a note.
    if row is None or not any(row[field] for field in NOTE_FIELDS):
        return None
    return {
        "requirement_id": row["id"], "match": match, "score": round(score, 2), "company": row["company"],
        **{field: row[field] for field in NOTE_FIELDS},
    }


Clients = list[tuple[int, Prepared, str]]


def _client_candidates(db: Database) -> Clients:
    return [(row["id"], prepare(row["customer"]), row["customer"]) for row in db.execute("SELECT id, customer FROM clients")]


def _match_clients(requirement: dict[str, Any], clients: Clients) -> tuple[list[int], dict[str, Any] | None]:
    """Clients that match the company exactly, and the single best candidate to offer as a link."""
    variants = [prepare(name) for name in company_name_variants(requirement["company"], _aliases_list(requirement["aliases"]))]
    exact, best = best_match(variants, [(client_id, prepared) for client_id, prepared, _ in clients])
    names = {client_id: customer for client_id, _, customer in clients}
    if exact:
        return exact, {"id": exact[0], "customer": names[exact[0]], "score": 1.0}
    if best:
        return [], {"id": best[0], "customer": names[best[0]], "score": round(best[1], 2)}
    return [], None


def _public(row: dict[str, Any], clients: Clients) -> dict[str, Any]:
    suggestion = _match_clients(row, clients)[1] if row["client_id"] is None else None
    public = {key: value for key, value in row.items() if key not in {"company_key", "aliases"}}
    public["aliases"] = _aliases_list(row["aliases"])
    public["suggested_client"] = suggestion
    return public


def _fetch_public(db: Database, requirement_id: int) -> dict[str, Any]:
    row = db.execute(
        """SELECT r.*, c.customer AS client_name FROM client_requirements r
           LEFT JOIN clients c ON c.id = r.client_id WHERE r.id = ?""", (requirement_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Requirement not found.")
    return _public(dict(row), _client_candidates(db))


@router.get("/api/client-requirements")
def list_client_requirements() -> list[dict[str, Any]]:
    with connect() as db:
        clients = _client_candidates(db)
        return [_public(row, clients) for row in _load_requirements(db)]


@router.post("/api/client-requirements/import")
async def import_client_requirements(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Upload an Excel .xlsx file.")
    records, errors = parse_requirements_excel(await file.read())
    if not records and errors:
        raise HTTPException(400, errors[0])
    created = updated = linked = suggested = 0
    with connect() as db:
        clients = _client_candidates(db)
        for record in records:
            timestamp = now_iso()
            key = name_key(record["company"])
            values = [record[field] for field in REQUIREMENT_TEXT_FIELDS]
            existing = db.execute("SELECT * FROM client_requirements WHERE company_key = ?", (key,)).fetchone()
            if existing:
                # The workbook is the source of truth for the text fields; manual aliases and links are kept.
                db.execute(
                    f"UPDATE client_requirements SET company=?, {_TEXT_ASSIGNMENTS}, updated_at=? WHERE id=?",
                    (record["company"], *values, timestamp, existing["id"]),
                )
                row_id, client_id, aliases = existing["id"], existing["client_id"], existing["aliases"]
                updated += 1
            else:
                row_id = db.insert_returning_id(
                    f"""INSERT INTO client_requirements (company, company_key, {_TEXT_COLUMNS}, created_at, updated_at)
                        VALUES (?, ?, {', '.join('?' for _ in REQUIREMENT_TEXT_FIELDS)}, ?, ?)
                        RETURNING id""",
                    (record["company"], key, *values, timestamp, timestamp),
                )
                client_id, aliases = None, ""
                created += 1
            if client_id is not None:
                linked += 1
                continue
            exact, suggestion = _match_clients({"company": record["company"], "aliases": aliases}, clients)
            if len(exact) == 1:  # only an unambiguous exact match is linked automatically
                db.execute("UPDATE client_requirements SET client_id = ? WHERE id = ?", (exact[0], row_id))
                linked += 1
            elif suggestion:
                suggested += 1
    total = created + updated
    return {
        "imported": total, "created": created, "updated": updated,
        "linked": linked, "suggested": suggested, "unlinked": total - linked - suggested, "errors": errors,
    }


class ClientRequirementPayload(BaseModel):
    company: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    client_id: int | None = None
    invoice_email: str = Field(default="", max_length=2000)
    payment: str = Field(default="", max_length=2000)
    pricing_requirements: str = Field(default="", max_length=4000)
    docs_required: str = Field(default="", max_length=4000)
    courier: str = Field(default="", max_length=2000)
    comments: str = Field(default="", max_length=4000)


@router.patch("/api/client-requirements/{requirement_id}")
def update_client_requirement(requirement_id: int, payload: ClientRequirementPayload) -> dict[str, Any]:
    company = payload.company.strip()
    if not name_key(company):
        raise HTTPException(422, "Company is required.")
    aliases = "\n".join(alias.strip() for alias in payload.aliases if alias.strip())
    with connect() as db:
        if not db.execute("SELECT 1 FROM client_requirements WHERE id = ?", (requirement_id,)).fetchone():
            raise HTTPException(404, "Requirement not found.")
        if payload.client_id is not None and not db.execute("SELECT 1 FROM clients WHERE id = ?", (payload.client_id,)).fetchone():
            raise HTTPException(422, "The selected client does not exist.")
        try:
            db.execute(
                f"UPDATE client_requirements SET company=?, company_key=?, aliases=?, client_id=?, {_TEXT_ASSIGNMENTS}, updated_at=? WHERE id=?",
                (company, name_key(company), aliases, payload.client_id,
                 *(getattr(payload, field).strip() for field in REQUIREMENT_TEXT_FIELDS), now_iso(), requirement_id),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "Another requirement already uses this company name.") from exc
        return _fetch_public(db, requirement_id)


@router.delete("/api/client-requirements")
def delete_all_client_requirements() -> dict[str, int]:
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM client_requirements").fetchone()[0]
        db.execute("DELETE FROM client_requirements")
    return {"deleted_requirements": count}


@router.delete("/api/client-requirements/{requirement_id}")
def delete_client_requirement(requirement_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT company FROM client_requirements WHERE id = ?", (requirement_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Requirement not found.")
        db.execute("DELETE FROM client_requirements WHERE id = ?", (requirement_id,))
    return {"deleted_requirement_id": requirement_id, "company": row["company"]}

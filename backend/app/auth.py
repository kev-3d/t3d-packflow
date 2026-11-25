"""Authentication with Clerk session tokens and role-based access control (admin / user).

Clerk owns identities: sign-up, sign-in (Google or email + password), password resets,
invitations and sessions. This module only:

- verifies the Clerk session JWT sent as ``Authorization: Bearer <token>`` on every API call,
- maps it to a row of the local ``users`` table, the only source of roles and of the
  active flag (a token never grants a role by itself),
- provisions first-time users: a row created by an admin (invitation) is linked by verified
  email; otherwise a verified email from an allowed domain becomes a ``user``.

Configuration (environment):
    CLERK_PUBLISHABLE_KEY     pk_test_... / pk_live_...; the issuer and JWKS URL derive from it.
    CLERK_SECRET_KEY          sk_...; used for the Clerk Backend API (user lookup, invitations).
    CLERK_AUTHORIZED_PARTIES  Optional comma-separated origins accepted in the ``azp`` claim.
    CLERK_JWKS_URL            Optional override of the JWKS endpoint.
    AUTH_ALLOWED_EMAIL_DOMAINS  Comma-separated domains that may sign in without an invitation.
    AUTH_BOOTSTRAP_ADMINS     Comma-separated emails created as admins at startup if missing.

Fails closed: without Clerk keys every protected endpoint answers 503.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request

from .database import IntegrityError, connect, now_iso

PUBLIC_PATHS = frozenset({"/api/health", "/api/auth/config"})
CLERK_API_URL = "https://api.clerk.com/v1"
ROLES = ("admin", "user")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOKEN_LEEWAY_SECONDS = 5


@dataclass(frozen=True)
class User:
    id: int
    email: str
    full_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "is_admin": self.is_admin}


class ClerkApiError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------- Configuration ----------

def _csv_env(name: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


def publishable_key() -> str:
    # VITE_CLERK_PUBLISHABLE_KEY is the name Clerk's React/Vite quickstart uses; accept it too.
    return (os.getenv("CLERK_PUBLISHABLE_KEY") or os.getenv("VITE_CLERK_PUBLISHABLE_KEY") or "").strip()


def secret_key() -> str:
    return os.getenv("CLERK_SECRET_KEY", "").strip()


def is_configured() -> bool:
    return not configuration_problems()


def configuration_problems() -> list[str]:
    """Human-readable reasons why sign-in cannot work (empty when it is configured)."""
    problems = []
    key = publishable_key()
    if not key:
        problems.append("CLERK_PUBLISHABLE_KEY is not set")
    else:
        try:
            frontend_api_host(key)
        except (ValueError, UnicodeDecodeError):
            problems.append("CLERK_PUBLISHABLE_KEY is not a valid pk_test_/pk_live_ key")
    secret = secret_key()
    if not secret:
        problems.append("CLERK_SECRET_KEY is not set")
    elif not secret.startswith(("sk_test_", "sk_live_")):
        problems.append("CLERK_SECRET_KEY is not a valid sk_test_/sk_live_ key")
    elif key and key.startswith("pk_test_") != secret.startswith("sk_test_"):
        problems.append("the publishable and secret keys belong to different instances (test vs live)")
    return problems


def frontend_api_host(key: str) -> str:
    """Clerk encodes its Frontend API host in the publishable key: pk_<env>_<base64(host + '$')>."""
    match = re.fullmatch(r"pk_(?:test|live)_([A-Za-z0-9+/=_-]+)", key)
    if not match:
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a valid Clerk publishable key.")
    encoded = match.group(1)
    decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    if not decoded.endswith("$"):
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a valid Clerk publishable key.")
    return decoded[:-1]


def issuer() -> str:
    return f"https://{frontend_api_host(publishable_key())}"


def allowed_email_domains() -> set[str]:
    return {domain.lower().lstrip("@") for domain in _csv_env("AUTH_ALLOWED_EMAIL_DOMAINS")}


def authorized_parties() -> list[str]:
    return [party.rstrip("/") for party in _csv_env("CLERK_AUTHORIZED_PARTIES")]


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


# ---------- Token verification ----------

_jwk_clients: dict[str, jwt.PyJWKClient] = {}


def _signing_key(token: str) -> Any:
    """Public key that signed ``token``, from Clerk's JWKS (cached for an hour)."""
    url = os.getenv("CLERK_JWKS_URL", "").strip() or f"{issuer()}/.well-known/jwks.json"
    client = _jwk_clients.get(url)
    if client is None:
        client = _jwk_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)
    return client.get_signing_key_from_jwt(token).key


def verify_token(token: str) -> dict[str, Any]:
    """Validate a Clerk session token (signature, exp/nbf, issuer, azp). Raises 401/503."""
    try:
        key = _signing_key(token)
    except jwt.PyJWKClientConnectionError as error:
        raise HTTPException(503, "The sign-in service is unreachable. Try again in a moment.") from error
    except (jwt.PyJWTError, ValueError) as error:
        raise _unauthorized() from error
    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"], issuer=issuer(), leeway=TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise _unauthorized() from error
    parties = authorized_parties()
    if parties and str(claims.get("azp", "")).rstrip("/") not in parties:
        raise _unauthorized()
    return claims


def _unauthorized(detail: str = "Sign in to continue.") -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


# ---------- Clerk Backend API ----------

def clerk_api(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{CLERK_API_URL}{path}", data=body, method=method,
        headers={
            "Authorization": f"Bearer {secret_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "packflow-backend",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise ClerkApiError(_clerk_error_message(error), error.code) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ClerkApiError("Clerk could not be reached.") from error


def _clerk_error_message(error: urllib.error.HTTPError) -> str:
    try:
        errors = json.loads(error.read() or b"{}").get("errors") or []
        if errors:
            return errors[0].get("long_message") or errors[0].get("message") or f"Clerk error {error.code}."
    except (ValueError, AttributeError):
        pass
    return f"Clerk error {error.code}."


def fetch_clerk_user(clerk_user_id: str) -> dict[str, Any]:
    return clerk_api("GET", f"/users/{urllib.parse.quote(clerk_user_id, safe='')}")


def send_invitation(email: str) -> None:
    payload: dict[str, Any] = {"email_address": email, "notify": True, "ignore_existing": True}
    redirect_url = os.getenv("AUTH_APP_URL", "").strip()
    if redirect_url:
        payload["redirect_url"] = redirect_url
    clerk_api("POST", "/invitations", payload)


def primary_verified_email(clerk_user: dict[str, Any]) -> str | None:
    primary_id = clerk_user.get("primary_email_address_id")
    for address in clerk_user.get("email_addresses") or []:
        verified = (address.get("verification") or {}).get("status") == "verified"
        if address.get("id") == primary_id and verified and address.get("email_address"):
            return normalize_email(address["email_address"])
    return None


def _clerk_full_name(clerk_user: dict[str, Any]) -> str:
    return " ".join(part for part in (clerk_user.get("first_name"), clerk_user.get("last_name")) if part).strip()


# ---------- Local users ----------

def _user_from_row(row: Any) -> User:
    return User(id=int(row["id"]), email=row["email"], full_name=row["full_name"], role=row["role"])


def resolve_user(claims: dict[str, Any]) -> User:
    """Local user for verified token claims, provisioning on first sign-in. Raises 403/503."""
    clerk_user_id = str(claims["sub"])
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE clerk_user_id = ?", (clerk_user_id,)).fetchone()
    if row is None:
        row = _provision(clerk_user_id)
    if not row["is_active"]:
        raise HTTPException(403, "Your account is disabled. Ask an administrator to restore access.")
    return _user_from_row(row)


def _provision(clerk_user_id: str) -> Any:
    try:
        clerk_user = fetch_clerk_user(clerk_user_id)
    except ClerkApiError as error:
        raise HTTPException(503, "Your account could not be checked with the sign-in service. Try again.") from error
    email = primary_verified_email(clerk_user)
    if email is None:
        raise HTTPException(403, "Verify your email address before using PackFlow.")
    timestamp = now_iso()
    try:
        with connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row is not None:
                # Invited by an admin (or re-created in Clerk): the verified email proves ownership.
                db.execute(
                    """UPDATE users SET clerk_user_id = ?, updated_at = ?,
                           full_name = CASE WHEN full_name = '' THEN ? ELSE full_name END
                       WHERE id = ?""",
                    (clerk_user_id, timestamp, _clerk_full_name(clerk_user), row["id"]),
                )
            elif email.rsplit("@", 1)[1] in allowed_email_domains():
                db.execute(
                    """INSERT INTO users (clerk_user_id, email, full_name, role, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, 'user', 1, ?, ?)""",
                    (clerk_user_id, email, _clerk_full_name(clerk_user), timestamp, timestamp),
                )
            else:
                raise HTTPException(403, "Your account does not have access to PackFlow. Ask an administrator to invite you.")
    except IntegrityError:
        pass  # A concurrent request provisioned the same user first.
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE clerk_user_id = ?", (clerk_user_id,)).fetchone()
    if row is None:
        raise HTTPException(403, "Your account does not have access to PackFlow. Ask an administrator.")
    return row


def bootstrap_admins() -> None:
    """Create the AUTH_BOOTSTRAP_ADMINS emails as admins when they do not exist yet.

    Existing rows are never changed, so a role edited in the app is not reverted on restart.
    """
    emails = [normalize_email(email) for email in _csv_env("AUTH_BOOTSTRAP_ADMINS")]
    emails = [email for email in emails if is_valid_email(email)]
    if not emails:
        return
    timestamp = now_iso()
    with connect() as db:
        for email in emails:
            db.execute(
                """INSERT INTO users (email, full_name, role, is_active, created_at, updated_at)
                   VALUES (?, '', 'admin', 1, ?, ?) ON CONFLICT (email) DO NOTHING""",
                (email, timestamp, timestamp),
            )


# ---------- FastAPI dependencies ----------

def current_user(request: Request) -> User | None:
    """Global dependency: every route requires a signed-in, active user except PUBLIC_PATHS."""
    if request.url.path in PUBLIC_PATHS:
        return None
    if not is_configured():
        raise HTTPException(503, f"Sign-in is not configured on this server: {'; '.join(configuration_problems())}.")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    user = resolve_user(verify_token(token.strip()))
    request.state.user = user
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise _unauthorized()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "This action requires an administrator.")
    return user

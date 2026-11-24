"""Grant administrator access to an email (creates the user or promotes an existing one).

Usage (from backend/, with the same DATABASE_URL / PACKFLOW_DB_PATH as the server):
    venv/Scripts/python -m scripts.create_admin admin@company.com --name "Full Name"
    venv/Scripts/python -m scripts.create_admin admin@company.com --no-invite

The person then signs in through Clerk with that email (Google or email + password); the
account is linked on the first sign-in. Unless --no-invite is given, a Clerk invitation
email is also sent (needs CLERK_SECRET_KEY). Safe to run again: it is idempotent.

On hosts without a shell (e.g. Render free), set AUTH_BOOTSTRAP_ADMINS instead.
"""
from __future__ import annotations

import argparse
import sys

from app.auth import ClerkApiError, is_valid_email, normalize_email, secret_key, send_invitation
from app.database import connect, init_db, now_iso
from app.env import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--name", default="", help="Full name shown in the app.")
    parser.add_argument("--no-invite", action="store_true", help="Do not send a Clerk invitation email.")
    args = parser.parse_args()

    load_env(prefix=("CLERK_", "AUTH_"))
    email = normalize_email(args.email)
    if not is_valid_email(email):
        print(f"Invalid email: {args.email}", file=sys.stderr)
        return 2

    init_db()
    timestamp = now_iso()
    with connect() as db:
        row = db.execute("SELECT id, role, is_active FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            db.execute(
                """INSERT INTO users (email, full_name, role, is_active, created_at, updated_at)
                   VALUES (?, ?, 'admin', 1, ?, ?)""",
                (email, args.name.strip(), timestamp, timestamp),
            )
            print(f"Created administrator {email}.")
        else:
            db.execute(
                """UPDATE users SET role = 'admin', is_active = 1, updated_at = ?,
                       full_name = CASE WHEN ? <> '' THEN ? ELSE full_name END
                   WHERE id = ?""",
                (timestamp, args.name.strip(), args.name.strip(), row["id"]),
            )
            print(f"{email} is now an active administrator.")

    if args.no_invite:
        return 0
    if not secret_key():
        print("CLERK_SECRET_KEY is not set: no invitation sent. The admin can still sign in with this email.")
        return 0
    try:
        send_invitation(email)
    except ClerkApiError as error:
        print(f"The Clerk invitation could not be sent: {error}", file=sys.stderr)
        return 1
    print(f"Clerk invitation sent to {email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

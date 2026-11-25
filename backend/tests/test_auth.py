"""Authentication (Clerk session tokens) and role-based access control."""
import base64
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, database
from app.main import app

HOST = "clerk.packflow.test"
ISSUER = f"https://{HOST}"
PUBLISHABLE_KEY = "pk_test_" + base64.b64encode(f"{HOST}$".encode()).decode().rstrip("=")
ORIGIN = "http://localhost:5173"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ENV = {
    "CLERK_PUBLISHABLE_KEY": PUBLISHABLE_KEY,
    "CLERK_SECRET_KEY": "sk_test_dummy",
    "CLERK_AUTHORIZED_PARTIES": ORIGIN,
    "AUTH_ALLOWED_EMAIL_DOMAINS": "company.com",
    "AUTH_BOOTSTRAP_ADMINS": "",
}


def make_token(sub: str, key=SIGNING_KEY, **overrides) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": ISSUER, "iat": now, "nbf": now, "exp": now + 60, "azp": ORIGIN, **overrides}
    return jwt.encode(claims, key, algorithm="RS256")


def clerk_user(email: str, verified: bool = True, first: str = "Ana", last: str = "Pérez") -> dict:
    return {
        "first_name": first, "last_name": last, "primary_email_address_id": "idn_1",
        "email_addresses": [{"id": "idn_1", "email_address": email,
                             "verification": {"status": "verified" if verified else "unverified"}}],
    }


def _calls(dependant) -> set:
    found = set()
    for dependency in dependant.dependencies:
        found.add(dependency.call)
        found |= _calls(dependency)
    return found


def api_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in sorted(route.methods):
                yield method, route


def concrete(path: str) -> str:
    """A requestable URL for a route path: every path parameter becomes 1."""
    return re.sub(r"\{[^}]+\}", "1", path.replace("{catalog_type}", "products"))


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "auth.db"
        database.init_db()
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.signing = mock.patch.object(auth, "_signing_key", lambda token: SIGNING_KEY.public_key())
        self.signing.start()
        self.clerk_users: dict[str, dict] = {}
        self.fetch = mock.patch.object(auth, "fetch_clerk_user", side_effect=self._fetch_clerk_user)
        self.fetch_mock = self.fetch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.fetch.stop()
        self.signing.stop()
        self.env.stop()
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def _fetch_clerk_user(self, clerk_user_id):
        if clerk_user_id not in self.clerk_users:
            raise auth.ClerkApiError("not found", 404)
        return self.clerk_users[clerk_user_id]

    def headers(self, sub: str, **overrides) -> dict:
        return {"Authorization": f"Bearer {make_token(sub, **overrides)}"}

    def sign_in(self, sub: str, email: str, **kwargs) -> dict:
        self.clerk_users[sub] = clerk_user(email, **kwargs)
        return self.headers(sub)

    def add_user(self, email: str, role: str = "user", active: bool = True) -> None:
        with database.connect() as db:
            db.execute(
                """INSERT INTO users (email, full_name, role, is_active, created_at, updated_at)
                   VALUES (?, '', ?, ?, 'now', 'now')""", (email, role, 1 if active else 0))

    def admin_headers(self) -> dict:
        self.add_user("boss@company.com", "admin")
        return self.sign_in("user_admin", "boss@company.com")


class TokenTests(AuthTestCase):
    def test_public_endpoints_need_no_token(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        config = self.client.get("/api/auth/config").json()
        self.assertEqual(config, {"configured": True, "publishable_key": PUBLISHABLE_KEY})

    def test_every_other_api_route_requires_a_token(self):
        for method, route in api_routes():
            if route.path in auth.PUBLIC_PATHS:
                continue
            with self.subTest(method=method, path=route.path):
                response = self.client.request(method, concrete(route.path))
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_fails_closed_without_clerk_keys(self):
        with mock.patch.dict(os.environ, {"CLERK_SECRET_KEY": ""}):
            self.assertEqual(self.client.get("/api/orders", headers=self.headers("user_x")).status_code, 503)
            self.assertEqual(self.client.get("/api/auth/config").json()["configured"], False)

    def test_invalid_tokens_are_rejected(self):
        self.sign_in("user_ana", "ana@company.com")
        cases = {
            "expired": make_token("user_ana", exp=int(time.time()) - 60, iat=int(time.time()) - 120),
            "wrong issuer": make_token("user_ana", iss="https://evil.example"),
            "wrong signature": make_token("user_ana", key=OTHER_KEY),
            "wrong azp": make_token("user_ana", azp="https://evil.example"),
            "garbage": "not-a-jwt",
        }
        for name, token in cases.items():
            with self.subTest(name):
                response = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": "Basic abc"}).status_code, 401)

    def test_azp_is_not_checked_when_no_parties_are_configured(self):
        headers = self.sign_in("user_ana", "ana@company.com")
        with mock.patch.dict(os.environ, {"CLERK_AUTHORIZED_PARTIES": ""}):
            response = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {make_token('user_ana', azp='http://other')}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 200)

    def test_configuration_problems_are_explained(self):
        self.assertEqual(auth.configuration_problems(), [])
        cases = {
            "CLERK_PUBLISHABLE_KEY is not set": {"CLERK_PUBLISHABLE_KEY": ""},
            "CLERK_PUBLISHABLE_KEY is not a valid pk_test_/pk_live_ key": {"CLERK_PUBLISHABLE_KEY": "pk_test_bm90LWEta2V5"},
            "CLERK_SECRET_KEY is not a valid sk_test_/sk_live_ key": {"CLERK_SECRET_KEY": "pk_test_wrong_field"},
            "the publishable and secret keys belong to different instances (test vs live)": {"CLERK_SECRET_KEY": "sk_live_x"},
        }
        for problem, env in cases.items():
            with self.subTest(problem), mock.patch.dict(os.environ, env):
                self.assertIn(problem, auth.configuration_problems())
                response = self.client.get("/api/orders", headers=self.headers("user_x"))
                self.assertEqual(response.status_code, 503)
                self.assertIn(problem, response.json()["detail"])

    def test_vite_publishable_key_name_is_accepted(self):
        with mock.patch.dict(os.environ, {"CLERK_PUBLISHABLE_KEY": "", "VITE_CLERK_PUBLISHABLE_KEY": PUBLISHABLE_KEY}):
            self.assertTrue(auth.is_configured())
            self.assertEqual(self.client.get("/api/auth/config").json()["publishable_key"], PUBLISHABLE_KEY)

    def test_issuer_derives_from_publishable_key(self):
        self.assertEqual(auth.frontend_api_host(PUBLISHABLE_KEY), HOST)
        with self.assertRaises(ValueError):
            auth.frontend_api_host("sk_test_abc")


class ProvisioningTests(AuthTestCase):
    def test_allowed_domain_with_verified_email_becomes_user(self):
        response = self.client.get("/api/auth/me", headers=self.sign_in("user_ana", "Ana@Company.com"))
        self.assertEqual(response.status_code, 200)
        me = response.json()
        self.assertEqual((me["email"], me["role"], me["is_admin"], me["full_name"]), ("ana@company.com", "user", False, "Ana Pérez"))
        # Linked: later requests do not call Clerk again.
        self.fetch_mock.reset_mock()
        self.assertEqual(self.client.get("/api/orders", headers=self.headers("user_ana")).status_code, 200)
        self.fetch_mock.assert_not_called()

    def test_other_domains_need_an_invitation(self):
        response = self.client.get("/api/auth/me", headers=self.sign_in("user_eve", "eve@gmail.com"))
        self.assertEqual(response.status_code, 403)
        with database.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM users WHERE email = 'eve@gmail.com'").fetchone())

    def test_unverified_email_is_rejected(self):
        response = self.client.get("/api/auth/me", headers=self.sign_in("user_ana", "ana@company.com", verified=False))
        self.assertEqual(response.status_code, 403)

    def test_invited_email_is_linked_with_its_role(self):
        self.add_user("partner@gmail.com", "admin")
        response = self.client.get("/api/auth/me", headers=self.sign_in("user_partner", "partner@gmail.com"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_admin"])
        with database.connect() as db:
            row = db.execute("SELECT clerk_user_id FROM users WHERE email = 'partner@gmail.com'").fetchone()
        self.assertEqual(row["clerk_user_id"], "user_partner")

    def test_disabled_user_is_rejected(self):
        self.add_user("old@company.com", "user", active=False)
        response = self.client.get("/api/auth/me", headers=self.sign_in("user_old", "old@company.com"))
        self.assertEqual(response.status_code, 403)

    def test_clerk_outage_is_a_503_not_a_denial(self):
        with mock.patch.object(auth, "fetch_clerk_user", side_effect=auth.ClerkApiError("down")):
            self.assertEqual(self.client.get("/api/auth/me", headers=self.headers("user_new")).status_code, 503)

    def test_bootstrap_admins_creates_missing_admins_only(self):
        self.add_user("demoted@company.com", "user")
        with mock.patch.dict(os.environ, {"AUTH_BOOTSTRAP_ADMINS": "First@company.com, demoted@company.com, bad"}):
            auth.bootstrap_admins()
            auth.bootstrap_admins()
        with database.connect() as db:
            roles = dict(db.execute("SELECT email, role FROM users").fetchall())
        self.assertEqual(roles, {"demoted@company.com": "user", "first@company.com": "admin"})


class RoleTests(AuthTestCase):
    def admin_only_routes(self):
        return [(method, route) for method, route in api_routes() if auth.require_admin in _calls(route.dependant)]

    def test_matrix_admin_only_routes(self):
        expected = {
            ("DELETE", "/api/orders"), ("DELETE", "/api/orders/{order_id}"), ("PUT", "/api/settings/exporter"),
            ("POST", "/api/catalog/products"), ("POST", "/api/catalog/boxes"),
            ("PUT", "/api/catalog/products/{product_id}"), ("PUT", "/api/catalog/boxes/{box_id}"),
            ("DELETE", "/api/catalog/products/{product_id}"), ("DELETE", "/api/catalog/boxes/{box_id}"),
            ("POST", "/api/catalog/{catalog_type}/import"), ("POST", "/api/clients/import"),
            ("PATCH", "/api/clients/{client_id}"), ("DELETE", "/api/clients"), ("DELETE", "/api/clients/{client_id}"),
            ("POST", "/api/client-requirements/import"), ("PATCH", "/api/client-requirements/{requirement_id}"),
            ("DELETE", "/api/client-requirements"), ("DELETE", "/api/client-requirements/{requirement_id}"),
            ("DELETE", "/api/shipments/{shipment_id}"),
            ("GET", "/api/admin/users"), ("POST", "/api/admin/users"),
            ("PATCH", "/api/admin/users/{user_id}"), ("POST", "/api/admin/users/{user_id}/invitation"),
        }
        self.assertEqual({(method, route.path) for method, route in self.admin_only_routes()}, expected)

    def test_users_get_403_and_admins_pass_on_admin_routes(self):
        user = self.sign_in("user_ana", "ana@company.com")
        admin = self.admin_headers()
        for method, route in self.admin_only_routes():
            path = concrete(route.path)
            with self.subTest(method=method, path=route.path):
                self.assertEqual(self.client.request(method, path, headers=user).status_code, 403)
                self.assertNotIn(self.client.request(method, path, headers=admin).status_code, (401, 403))

    def test_users_run_the_operational_workflow(self):
        user = self.sign_in("user_ana", "ana@company.com")
        created = self.client.post("/api/orders/email", headers=user,
                                   json={"text": "Guantes de nitrilo · caja - 2 pcs", "client": "QA"})
        self.assertEqual(created.status_code, 201, created.text)
        order_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/orders/{order_id}/analyze", headers=user).status_code, 200)
        report = self.client.post(f"/api/orders/{order_id}/reports", headers=user)
        self.assertEqual(report.status_code, 201)
        self.assertEqual(self.client.get(report.json()["download_url"], headers=user).status_code, 200)
        self.assertEqual(self.client.get("/api/catalog/products", headers=user).status_code, 200)
        self.assertEqual(self.client.get("/api/clients", headers=user).status_code, 200)


class AdminUserTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_headers()
        self.invite = mock.patch.object(auth, "send_invitation")
        self.invite_mock = self.invite.start()
        import app.auth_routes as routes
        self.routes_invite = mock.patch.object(routes, "send_invitation", self.invite_mock)
        self.routes_invite.start()

    def tearDown(self):
        self.routes_invite.stop()
        self.invite.stop()
        super().tearDown()

    def my_id(self) -> int:
        return self.client.get("/api/auth/me", headers=self.admin).json()["id"]

    def test_create_invite_and_reject_duplicates(self):
        response = self.client.post("/api/admin/users", headers=self.admin,
                                    json={"email": " New@Partner.com ", "full_name": "New", "role": "user"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["invitation"], "sent")
        self.assertEqual(response.json()["user"]["email"], "new@partner.com")
        self.invite_mock.assert_called_once_with("new@partner.com")
        duplicate = self.client.post("/api/admin/users", headers=self.admin, json={"email": "new@partner.com"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.client.post("/api/admin/users", headers=self.admin, json={"email": "nope"}).status_code, 422)

    def test_failed_invitation_still_grants_access(self):
        self.invite_mock.side_effect = auth.ClerkApiError("quota")
        response = self.client.post("/api/admin/users", headers=self.admin, json={"email": "x@partner.com"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json()["invitation"], response.json()["invitation_error"]), ("failed", "quota"))

    def test_admin_cannot_remove_own_access(self):
        my_id = self.my_id()
        for change in ({"role": "user"}, {"is_active": False}):
            with self.subTest(change):
                self.assertEqual(self.client.patch(f"/api/admin/users/{my_id}", headers=self.admin, json=change).status_code, 409)

    def test_promote_demote_and_disable(self):
        user = self.sign_in("user_ana", "ana@company.com")
        ana_id = self.client.get("/api/auth/me", headers=user).json()["id"]
        promoted = self.client.patch(f"/api/admin/users/{ana_id}", headers=self.admin, json={"role": "admin"})
        self.assertEqual(promoted.json()["role"], "admin")
        self.assertEqual(self.client.get("/api/admin/users", headers=user).status_code, 200)
        # With two admins, the first can be demoted by the second.
        boss_id = self.my_id()
        self.assertEqual(self.client.patch(f"/api/admin/users/{boss_id}", headers=user, json={"role": "user"}).status_code, 200)
        # Ana is now the last active admin: nobody can disable her (and the old admin no longer can try).
        self.assertEqual(self.client.patch(f"/api/admin/users/{ana_id}", headers=self.admin, json={"is_active": False}).status_code, 403)
        disabled = self.client.patch(f"/api/admin/users/{boss_id}", headers=user, json={"is_active": False})
        self.assertFalse(disabled.json()["is_active"])
        self.assertEqual(self.client.get("/api/orders", headers=self.admin).status_code, 403)

    def test_disabled_admin_can_be_demoted(self):
        # Only active admins count for the "last admin" rule.
        self.add_user("second@company.com", "admin", active=False)
        with database.connect() as db:
            second_id = db.execute("SELECT id FROM users WHERE email = 'second@company.com'").fetchone()["id"]
        self.assertEqual(self.client.patch(f"/api/admin/users/{second_id}", headers=self.admin, json={"role": "user"}).status_code, 200)
        users = self.client.get("/api/admin/users", headers=self.admin).json()
        self.assertEqual({user["email"] for user in users}, {"boss@company.com", "second@company.com"})


if __name__ == "__main__":
    unittest.main()

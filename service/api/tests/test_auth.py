"""The API admits a request only with BOTH valid Cloudflare Access tokens.

No network: tokens are signed with a throwaway RSA key and the key lookup is
replaced, so these tests exercise the real verification (signature, audience,
issuer, expiry) without Cloudflare.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import auth
from app.settings import get_settings

TEAM = "test-team.cloudflareaccess.com"
API_AUD = "api-aud"
HUB_AUD = "hub-aud"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HUB_API_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("HUB_API_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("HUB_API_ACCESS_API_AUD", API_AUD)
    monkeypatch.setenv("HUB_API_ACCESS_HUB_AUD", HUB_AUD)
    get_settings.cache_clear()

    async def fake_key(token, settings):
        return KEY.public_key()

    monkeypatch.setattr(auth, "_signing_key", fake_key)
    # A bare app exercising only the sign-in check. /v1/me itself also needs the
    # database (email → Person → roles); that side is covered by test_access.py.
    from fastapi import Depends, FastAPI
    from app import errors
    app = FastAPI()
    errors.install(app)

    @app.get("/v1/me")
    async def me(user: auth.User = Depends(auth.current_user)):
        return {"email": user.email}

    yield TestClient(app)
    get_settings.cache_clear()


def token(aud, email=None, key=KEY, exp_in=300, iss=f"https://{TEAM}"):
    now = int(time.time())
    claims = {"aud": [aud], "iss": iss, "iat": now, "exp": now + exp_in}
    if email:
        claims["email"] = email
    return jwt.encode(claims, key, algorithm="RS256")


def headers(service=None, user=None):
    h = {}
    if service:
        h["Cf-Access-Jwt-Assertion"] = service
    if user:
        h["X-Hub-User-Jwt"] = user
    return h


def test_both_valid_tokens_identify_the_manager(client):
    r = client.get("/v1/me", headers=headers(token(API_AUD), token(HUB_AUD, "Defila@Noktah.co")))
    assert r.status_code == 200 and r.json() == {"email": "defila@noktah.co"}


@pytest.mark.parametrize("h", [
    {},                                                            # straight from the office network
    {"Cf-Access-Jwt-Assertion": "x"},                              # service token only, no person
    {"X-Hub-User-Jwt": "x"},                                       # person only, skipped Access
])
def test_missing_credentials_are_rejected(client, h):
    assert client.get("/v1/me", headers=h).status_code == 401


def test_a_forged_email_header_is_not_enough(client):
    h = headers(token(API_AUD), None)
    h["Cf-Access-Authenticated-User-Email"] = "bagas@noktah.co"
    assert client.get("/v1/me", headers=h).status_code == 401


def test_user_token_for_the_wrong_application_is_rejected(client):
    # A token minted for the API application must not pass as a Hub login.
    r = client.get("/v1/me", headers=headers(token(API_AUD), token(API_AUD, "defila@noktah.co")))
    assert r.status_code == 401


def test_token_signed_by_someone_else_is_rejected(client):
    r = client.get("/v1/me", headers=headers(token(API_AUD, key=OTHER_KEY), token(HUB_AUD, "defila@noktah.co")))
    assert r.status_code == 401


def test_expired_login_is_rejected(client):
    r = client.get("/v1/me", headers=headers(token(API_AUD), token(HUB_AUD, "defila@noktah.co", exp_in=-60)))
    assert r.status_code == 401


def test_token_from_another_access_team_is_rejected(client):
    r = client.get("/v1/me", headers=headers(token(API_AUD), token(HUB_AUD, "x@y.z", iss="https://evil.cloudflareaccess.com")))
    assert r.status_code == 401


def test_a_service_token_cannot_act_as_a_person(client):
    # Service-token JWTs carry no email.
    r = client.get("/v1/me", headers=headers(token(API_AUD), token(HUB_AUD)))
    assert r.status_code == 401

"""
Who is calling, proven by Cloudflare Access, never by a header we merely read.

Every request must carry two Access JWTs, both RS256-signed by our Access team:

1. `Cf-Access-Jwt-Assertion`: added by Cloudflare at hub-api.noktah.co after it
   accepted the Hub Worker's service token. Its audience is the API application.
   It proves the request came through Access, not straight from the office network
   or another container.
2. `X-Hub-User-Jwt`: the signed-in manager's own Access token, which Access gave the
   Worker at hub.noktah.co and the Worker forwards. Its audience is the Hub
   application. It proves WHICH manager is acting, so every change is recorded under a
   real, verified email. A plain email header could be forged by any caller that has
   the service token; this token cannot.

A missing, expired, wrongly-signed or wrong-audience token gets 401, never a default user.
A verified email with no Manager role gets 403 `no_access` (G-11); roles are the Hub's
own, from the people list, never Cloudflare's.
"""
from dataclasses import dataclass
from typing import Any, Dict, Tuple

import jwt
from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool

from . import db
from .errors import NoAccess, Unauthenticated
from .permissions import Assignment, is_manager
from .settings import Settings, get_settings

SERVICE_HEADER = "cf-access-jwt-assertion"
USER_HEADER = "x-hub-user-jwt"

_jwks_clients: Dict[str, jwt.PyJWKClient] = {}


def _jwks(url: str) -> jwt.PyJWKClient:
    # Keys are cached in-process; Cloudflare rotates them, and PyJWKClient refetches
    # when it sees a key id it doesn't know.
    if url not in _jwks_clients:
        _jwks_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
    return _jwks_clients[url]


async def _signing_key(token: str, settings: Settings):
    # PyJWKClient fetches over blocking urllib; keep it off the event loop.
    return await run_in_threadpool(lambda: _jwks(settings.access_certs_url).get_signing_key_from_jwt(token).key)


async def verify_access_jwt(token: str, audience: str, settings: Settings) -> Dict[str, Any]:
    """Claims of a valid Access JWT for `audience`; raises jwt.InvalidTokenError otherwise."""
    key = await _signing_key(token, settings)
    return jwt.decode(
        token, key, algorithms=["RS256"], audience=audience, issuer=settings.access_issuer,
        options={"require": ["exp", "iat", "aud", "iss"]},
    )


@dataclass(frozen=True)
class User:
    """A verified sign-in: the email Cloudflare Access proved. Not yet a Person."""
    email: str


async def current_user(request: Request, settings: Settings = Depends(get_settings)) -> User:
    service_token = request.headers.get(SERVICE_HEADER)
    user_token = request.headers.get(USER_HEADER)
    if not service_token or not user_token:
        raise Unauthenticated("Belum login melalui Cloudflare Access.")
    try:
        await verify_access_jwt(service_token, settings.access_api_aud, settings)
        claims = await verify_access_jwt(user_token, settings.access_hub_aud, settings)
    except jwt.InvalidTokenError as e:
        raise Unauthenticated(f"Token Cloudflare Access tidak valid: {e}") from e
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        # A service-token JWT has no email; only a person may act through the Hub.
        raise Unauthenticated("Token tidak menunjukkan seseorang.")
    return User(email=email)


@dataclass(frozen=True)
class Caller:
    """The Person behind a verified sign-in, with their active roles (G-11, G-16).

    History always records `person_id`, never the email (G-16).
    """
    person_id: str
    display_name: str
    email: str
    assignments: Tuple[Assignment, ...]


async def load_caller(email: str) -> Caller:
    """email → Person → active roles. NoAccess unless they hold a Manager role (G-9, G-11)."""
    async with db.pool().acquire() as conn:
        person = await conn.fetchrow(
            """SELECT p.id, p.display_name, p.status
               FROM person_emails e JOIN people p ON p.id = e.person_id
               WHERE e.email = $1""",
            email,
        )
        if person is None or person["status"] != "active":
            raise NoAccess("Anda belum punya akses ke Noktah Hub.", email=email)
        rows = await conn.fetch(
            """SELECT r.role, b.brand_key
               FROM person_roles r LEFT JOIN noktah_brands b ON b.id = r.noktah_brand_id
               WHERE r.person_id = $1 AND r.valid_to IS NULL""",
            person["id"],
        )
    assignments = tuple(Assignment(r["role"], r["brand_key"]) for r in rows)
    if not is_manager(assignments):
        raise NoAccess("Anda belum punya akses ke Noktah Hub.", email=email)
    return Caller(str(person["id"]), person["display_name"], email, assignments)


async def current_caller(user: User = Depends(current_user)) -> Caller:
    return await load_caller(user.email)

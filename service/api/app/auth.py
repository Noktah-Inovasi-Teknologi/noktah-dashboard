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
"""
from dataclasses import dataclass
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

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
    email: str


async def current_user(request: Request, settings: Settings = Depends(get_settings)) -> User:
    service_token = request.headers.get(SERVICE_HEADER)
    user_token = request.headers.get(USER_HEADER)
    if not service_token or not user_token:
        raise HTTPException(status_code=401, detail="missing Cloudflare Access credentials")
    try:
        await verify_access_jwt(service_token, settings.access_api_aud, settings)
        claims = await verify_access_jwt(user_token, settings.access_hub_aud, settings)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"invalid Cloudflare Access token: {e}") from e
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        # A service-token JWT has no email; only a person may act through the Hub.
        raise HTTPException(status_code=401, detail="token does not identify a person")
    return User(email=email)

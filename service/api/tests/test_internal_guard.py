"""/internal is reachable only with the token AND never through Cloudflare."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import errors, internal
from app.settings import get_settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HUB_API_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("HUB_API_ACCESS_API_AUD", "aud")
    monkeypatch.setenv("HUB_API_INTERNAL_TOKEN", "s3cret-token")
    get_settings.cache_clear()
    app = FastAPI()
    errors.install(app)
    app.include_router(internal.router)
    yield TestClient(app)
    get_settings.cache_clear()


def test_token_on_docker_network_is_allowed(client):
    assert client.get("/internal/ping", headers={"X-Hub-Internal-Token": "s3cret-token"}).status_code == 200


@pytest.mark.parametrize("headers", [
    {},
    {"X-Hub-Internal-Token": "wrong"},
    {"X-Hub-Internal-Token": ""},
])
def test_missing_or_wrong_token_is_hidden(client, headers):
    assert client.get("/internal/ping", headers=headers).status_code == 404


@pytest.mark.parametrize("cf_header", ["cf-ray", "cf-connecting-ip", "cf-access-jwt-assertion"])
def test_right_token_through_cloudflare_is_refused(client, cf_header):
    headers = {"X-Hub-Internal-Token": "s3cret-token", cf_header: "x"}
    assert client.get("/internal/ping", headers=headers).status_code == 404


def test_empty_configured_token_disables_internal(client, monkeypatch):
    monkeypatch.setenv("HUB_API_INTERNAL_TOKEN", "")
    get_settings.cache_clear()
    assert client.get("/internal/ping", headers={"X-Hub-Internal-Token": ""}).status_code == 404

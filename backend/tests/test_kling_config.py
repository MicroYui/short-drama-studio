"""Independent local credentials, safe responses and exact fallback semantics."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from _routes.video_edit import router
from short_drama.deps import get_short_drama_service
from short_drama.h3_models import H3ProviderConfigUpdate
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.kling_config import KlingProviderConfigStore, KlingProviderConfigUpdate
from short_drama.service import ShortDramaService


@pytest.fixture(autouse=True)
def credential_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KLING_API_KEY", "KLING_UPLOAD_TOKEN", "ACEDATACLOUD_API_KEY", "MINIMAX_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_missing_then_h3_fallback_then_independent_keys_and_exact_clear(tmp_path: Path) -> None:
    h3 = H3ProviderConfigStore(tmp_path / "h3.json")
    store = KlingProviderConfigStore(tmp_path / "kling.json", h3)
    assert store.response().key_source == "MISSING"
    assert not store.response().credentials_configured
    h3.update(H3ProviderConfigUpdate(api_key="h3-generation"))
    inherited = store.response()
    assert inherited.key_source == "H3_FALLBACK" and inherited.inherited_key_source == "LOCAL_CONFIG"
    assert inherited.upload_token_source == "API_KEY_FALLBACK"
    store.update(KlingProviderConfigUpdate(api_key=" own-generation ", upload_token=" own-upload "))
    assert store.api_key() == "own-generation" and store.upload_token() == "own-upload"
    assert store.path.stat().st_mode & 0o777 == 0o600
    before = store.path.read_bytes()
    store.update(KlingProviderConfigUpdate(api_key="   ", upload_token=""))
    assert store.path.read_bytes() == before
    response = store.update(KlingProviderConfigUpdate(clear_api_key=True))
    assert store.api_key() == "h3-generation" and store.upload_token() == "own-upload"
    assert response.key_source == "H3_FALLBACK" and not response.has_saved_api_key and response.has_saved_upload_token
    response = store.update(KlingProviderConfigUpdate(clear_upload_token=True))
    assert store.upload_token() == "h3-generation" and not response.has_saved_upload_token
    assert h3.api_key() == "h3-generation"
    serialized = response.model_dump_json()
    assert all(value not in serialized for value in ("h3-generation", "own-generation", "own-upload"))
    assert response.permissions_verified is False


def test_independent_environment_overrides_saved_fields_and_clear_does_not_remove_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h3 = H3ProviderConfigStore(tmp_path / "h3.json")
    store = KlingProviderConfigStore(tmp_path / "kling.json", h3)
    store.update(KlingProviderConfigUpdate(api_key="saved-key", upload_token="saved-upload"))
    monkeypatch.setenv("KLING_API_KEY", "env-key")
    monkeypatch.setenv("KLING_UPLOAD_TOKEN", "env-upload")
    assert store.api_key() == "env-key" and store.upload_token() == "env-upload"
    response = store.update(KlingProviderConfigUpdate(clear_api_key=True, clear_upload_token=True))
    assert response.key_source == "KLING_ENVIRONMENT" and response.upload_token_source == "KLING_UPLOAD_ENVIRONMENT"
    assert response.has_api_key and response.has_upload_token and not response.has_saved_api_key and not response.has_saved_upload_token
    monkeypatch.delenv("KLING_API_KEY")
    monkeypatch.delenv("KLING_UPLOAD_TOKEN")
    monkeypatch.setenv("ACEDATACLOUD_API_KEY", "h3-env-key")
    assert store.api_key() == "h3-env-key" and store.response().inherited_key_source == "ACEDATACLOUD_ENVIRONMENT"


def test_product_no_longer_exposes_kling_configuration(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    app = FastAPI()
    app.include_router(router, prefix="/api/short-drama")
    app.dependency_overrides[get_short_drama_service] = lambda: service
    endpoint = "/api/short-drama/providers/kling-lip-sync"
    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 404
        assert client.post(endpoint, json={"api_key": "unused-test-value"}).status_code == 404
    assert not service.kling_config.path.exists()

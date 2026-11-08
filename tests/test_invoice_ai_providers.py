"""AI tiekėjų (cli / vertex / anthropic) testai: httpx, google-auth ir subprocess užmokinti, tinklo nėra."""
import base64
import json
import subprocess

import httpx
import pytest

from core import invoice_ai

FIELDS = {"number": "AB-1", "net": "10.00"}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.request = httpx.Request("POST", "https://x")

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("klaida", request=self.request, response=self)  # type: ignore[arg-type]


@pytest.fixture
def posts(monkeypatch):
    calls: list[dict] = []
    queue: list = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(invoice_ai.httpx, "post", fake_post)
    monkeypatch.setattr(invoice_ai.time, "sleep", lambda s: None)
    calls_queue = type("P", (), {"calls": calls, "queue": queue})
    return calls_queue


# ---------- provider() ----------

def test_provider_default_is_cli():
    assert invoice_ai.provider() == "cli"


@pytest.mark.parametrize("value,expected", [("vertex", "vertex"), (" Anthropic ", "anthropic"),
                                            ("CLI", "cli"), ("gemini-free", "cli")])
def test_provider_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("UNO_AI_PROVIDER", value)
    assert invoice_ai.provider() == expected


def test_model_defaults_and_override(monkeypatch):
    assert invoice_ai.model_name("vertex") == "gemini-3.8-flash"
    assert invoice_ai.model_name("anthropic") == "claude-sonnet-5-5"
    assert invoice_ai.model_name("cli") == "sonnet"
    monkeypatch.setenv("UNO_AI_MODEL", "kitas-modelis")
    assert invoice_ai.model_name("vertex") == "kitas-modelis"


# ---------- anthropic ----------

def test_anthropic_request_and_parse(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    posts.queue.append(FakeResponse(200, {"content": [{"type": "text", "text": "```json\n" + json.dumps(FIELDS) + "\n```"}]}))
    assert invoice_ai._call_claude("sąskaitos tekstas") == FIELDS
    call = posts.calls[0]
    assert call["url"] == invoice_ai.ANTHROPIC_URL
    assert call["headers"]["x-api-key"] == "sk-test"
    assert call["headers"]["anthropic-version"] == invoice_ai.ANTHROPIC_VERSION
    assert call["json"]["model"] == "claude-sonnet-5-5"
    content = call["json"]["messages"][0]["content"]
    assert content.startswith(invoice_ai.PROMPT) and content.endswith("sąskaitos tekstas")


def test_anthropic_model_from_env(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("UNO_AI_MODEL", "claude-x")
    posts.queue.append(FakeResponse(200, {"content": [{"type": "text", "text": "{}"}]}))
    invoice_ai._call_claude("t")
    assert posts.calls[0]["json"]["model"] == "claude-x"


def test_anthropic_without_key_returns_none_without_http(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    assert invoice_ai._call_claude("t") is None
    assert posts.calls == []


def test_text_is_truncated(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    posts.queue.append(FakeResponse(200, {"content": [{"type": "text", "text": "{}"}]}))
    invoice_ai._call_claude("x" * (invoice_ai.MAX_TEXT_CHARS + 500))
    sent = posts.calls[0]["json"]["messages"][0]["content"]
    assert len(sent) == len(invoice_ai.PROMPT) + invoice_ai.MAX_TEXT_CHARS


def test_retry_on_429_then_success(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    posts.queue += [FakeResponse(429), FakeResponse(200, {"content": [{"type": "text", "text": json.dumps(FIELDS)}]})]
    assert invoice_ai._call_claude("t") == FIELDS
    assert len(posts.calls) == 2


def test_http_error_and_transport_error_give_none(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    posts.queue.append(FakeResponse(401))
    assert invoice_ai._call_claude("t") is None
    posts.queue += [httpx.ConnectError("x"), httpx.ConnectError("x")]
    assert invoice_ai._call_claude("t") is None


def test_garbage_response_gives_none(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    posts.queue.append(FakeResponse(200, {"content": [{"type": "text", "text": "nesamone"}]}))
    assert invoice_ai._call_claude("t") is None


# ---------- vertex ----------

def _vertex_ok(monkeypatch):
    monkeypatch.setenv("UNO_AI_PROVIDER", "vertex")
    monkeypatch.setattr(invoice_ai, "vertex_access_token", lambda: ("tok-123", "proj-1"))


def test_vertex_request_shape_and_parse(monkeypatch, posts):
    _vertex_ok(monkeypatch)
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(FIELDS)}]}}]}
    posts.queue.append(FakeResponse(200, body))
    assert invoice_ai._call_claude("tekstas") == FIELDS
    call = posts.calls[0]
    assert call["url"] == ("https://aiplatform.eu.rep.googleapis.com/v1/projects/proj-1/locations/eu"
                           "/publishers/google/models/gemini-3.8-flash:generateContent")
    assert call["headers"]["Authorization"] == "Bearer tok-123"
    assert call["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert call["json"]["contents"][0]["parts"][0]["text"].endswith("tekstas")


def test_vertex_skips_thought_parts(monkeypatch, posts):
    _vertex_ok(monkeypatch)
    parts = [{"text": "galvoju", "thought": True}, {"text": json.dumps(FIELDS)}]
    posts.queue.append(FakeResponse(200, {"candidates": [{"content": {"parts": parts}}]}))
    assert invoice_ai._call_claude("t") == FIELDS


def test_vertex_empty_candidates_gives_none(monkeypatch, posts):
    _vertex_ok(monkeypatch)
    posts.queue.append(FakeResponse(200, {"candidates": []}))
    assert invoice_ai._call_claude("t") is None


def test_vertex_location_and_model_env(monkeypatch, posts):
    _vertex_ok(monkeypatch)
    monkeypatch.setenv("UNO_AI_LOCATION", "europe-west1")
    monkeypatch.setenv("UNO_AI_MODEL", "gemini-9")
    posts.queue.append(FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}))
    invoice_ai._call_claude("t")
    assert posts.calls[0]["url"].startswith("https://europe-west1-aiplatform.googleapis.com/v1/projects/proj-1/locations/europe-west1/")
    assert posts.calls[0]["url"].endswith("/models/gemini-9:generateContent")


def test_vertex_url_global():
    assert invoice_ai.vertex_url("p", "global", "m").startswith("https://aiplatform.googleapis.com/v1/projects/p/locations/global/")


def test_vertex_auth_failure_gives_none(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "vertex")

    def boom():
        raise RuntimeError("nėra kredencialų")

    monkeypatch.setattr(invoice_ai, "vertex_access_token", boom)
    assert invoice_ai._call_claude("t") is None
    assert posts.calls == []


def test_vertex_access_token_uses_service_account_and_scope(monkeypatch):
    from google.oauth2 import service_account

    info = {"type": "service_account", "project_id": "from-key", "client_email": "a@b.iam"}
    monkeypatch.setenv("GOOGLE_SA_KEY_B64", base64.b64encode(json.dumps(info).encode()).decode())
    invoice_ai._cred_cache.clear()
    seen = {}

    class FakeCreds:
        valid = False
        token = None

        def refresh(self, request):
            self.token = "abc"
            self.valid = True

    def fake_from_info(i, scopes):
        seen["info"], seen["scopes"] = i, scopes
        return FakeCreds()

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", staticmethod(fake_from_info))
    assert invoice_ai.vertex_access_token() == ("abc", "from-key")
    assert seen["scopes"] == [invoice_ai.CLOUD_SCOPE] and seen["info"]["client_email"] == "a@b.iam"
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-proj")
    assert invoice_ai.vertex_access_token() == ("abc", "env-proj")  # žetonas pernaudotas, projektas iš env
    invoice_ai._cred_cache.clear()


# ---------- cli ----------

def test_cli_path_unchanged(monkeypatch):
    monkeypatch.setattr(invoice_ai.shutil, "which", lambda n: "claude")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw["input"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": json.dumps(FIELDS)}), stderr="")

    monkeypatch.setattr(invoice_ai.subprocess, "run", fake_run)
    assert invoice_ai._call_claude("t") == FIELDS
    assert seen["cmd"][-2:] == ["--model", "sonnet"]
    assert seen["input"].startswith(invoice_ai.PROMPT)


def test_cli_missing_gives_none(monkeypatch):
    monkeypatch.setattr(invoice_ai.shutil, "which", lambda n: None)
    assert invoice_ai._call_claude("t") is None


def test_empty_text_never_calls_provider(monkeypatch, posts):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert invoice_ai._call_claude("   ") is None
    assert posts.calls == []


def test_cache_shared_across_providers(monkeypatch, posts, tmp_path):
    monkeypatch.setenv("UNO_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    posts.queue.append(FakeResponse(200, {"content": [{"type": "text", "text": json.dumps(FIELDS)}]}))
    assert invoice_ai.extract_fields("tekstas", tmp_path) == FIELDS
    assert invoice_ai.extract_fields("tekstas", tmp_path) == FIELDS
    assert len(posts.calls) == 1


def test_s9_adc_project_cached_with_credentials(monkeypatch):
    import google.auth

    monkeypatch.delenv("GOOGLE_SA_KEY_B64", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    invoice_ai._cred_cache.clear()
    calls = []

    class FakeCreds:
        valid = True
        token = "tok"

    creds = FakeCreds()
    monkeypatch.setattr(google.auth, "default", lambda scopes: calls.append(scopes) or (creds, "adc-proj"))
    assert invoice_ai.vertex_access_token() == ("tok", "adc-proj")
    assert invoice_ai.vertex_access_token() == ("tok", "adc-proj")  # antras kvietimas: projektas iš talpyklos
    assert len(calls) == 1
    invoice_ai._cred_cache.clear()

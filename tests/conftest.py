"""Bendri testų nustatymai."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_supplier_store(monkeypatch, tmp_path):
    """Testai nemato tikros tiekėjų kartotekos (UNO_DATA per Windows naudotojo kintamuosius)."""
    monkeypatch.setenv("UNO_SUPPLIERS_FILE", str(tmp_path / "suppliers_test.json"))


@pytest.fixture(autouse=True)
def _no_ai_env(monkeypatch):
    """Testai nekviečia tikro AI: tiekėjo kintamieji išvalomi."""
    for name in ("UNO_AI_PROVIDER", "UNO_AI_MODEL", "UNO_AI_LOCATION", "ANTHROPIC_API_KEY", "GOOGLE_SA_KEY_B64"):
        monkeypatch.delenv(name, raising=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: tikras tinklas / tikra paslauga; leidžiama tik su UNO_LIVE=1")


def pytest_collection_modifyitems(config, items):
    import os
    if os.environ.get("UNO_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live testas: nustatykite UNO_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)

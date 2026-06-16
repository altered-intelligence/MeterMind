"""Runtime selection of the active Ollama model.

The active model can be changed from the UI at runtime and is persisted to a small file
under ``var/`` so the choice survives restarts. It overrides the ``OLLAMA_MODEL`` env
default. Also lists the models actually installed in the local Ollama.
"""

from __future__ import annotations

import logging

from ..config import get_settings

logger = logging.getLogger("hui.ollama")


def _model_file():
    """Path to the persisted model choice — follows the configured storage dir so it is
    isolated per environment (and per test)."""
    return get_settings().storage_dir.parent / ".ai_model"


def get_active_model() -> str:
    """The UI-selected model if set, otherwise the OLLAMA_MODEL default."""
    try:
        path = _model_file()
        if path.exists():
            val = path.read_text(encoding="utf-8").strip()
            if val:
                return val
    except OSError:
        pass
    return get_settings().ollama_model


def set_active_model(name: str) -> None:
    path = _model_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name.strip(), encoding="utf-8")


def resolve_model(name: str, available: list[str]) -> "str | None":
    """Match a model name to an installed tag, treating 'x' == 'x:latest'.

    Returns the canonical installed name, or None if not installed.
    """
    if not name:
        return None
    if name in available:
        return name
    if f"{name}:latest" in available:
        return f"{name}:latest"
    base = name.split(":")[0]
    for a in available:
        if a.split(":")[0] == base:
            return a
    return None


def list_installed_models() -> list[str]:
    """Model names installed in the local Ollama (via /api/tags). Empty if unreachable."""
    try:
        import httpx
    except Exception:
        return []
    host = get_settings().ollama_host.rstrip("/")
    try:
        resp = httpx.get(f"{host}/api/tags", timeout=5.0)
        if resp.status_code != 200:
            return []
        return sorted(m.get("name") for m in resp.json().get("models", []) if m.get("name"))
    except Exception:
        return []

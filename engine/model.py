"""Model adapter: the one place every LLM call goes through.

The model choice waits on the organisers' approved list, so the backend is
picked by environment variable and can be swapped without touching the rest
of the engine:

- CHAMELEON_OLLAMA_MODEL=<name>  a local Ollama server (default host
  http://127.0.0.1:11434, override with CHAMELEON_OLLAMA_HOST)
- unset                          no model: every call raises ModelUnavailable,
                                 and the engine runs on its preset logic
"""

import json
import os
import urllib.request
from typing import Protocol


class ModelUnavailable(Exception):
    """No model can be reached; retrying won't help."""


class ModelAdapter(Protocol):
    def complete(self, prompt: str) -> str:
        """Return the model's reply to `prompt`. Raises ModelUnavailable if
        no model can be reached."""
        ...


class NoModel:
    def complete(self, prompt: str) -> str:
        raise ModelUnavailable("no model configured (set CHAMELEON_OLLAMA_MODEL)")


class OllamaModel:
    """Deterministic JSON-mode completions from a local Ollama server."""

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 600):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def complete(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0, "seed": 0},
        }).encode()
        request = urllib.request.Request(f"{self.host}/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())["response"]
        except OSError as exc:
            raise ModelUnavailable(f"Ollama at {self.host} unreachable: {exc}") from exc


def default_model() -> ModelAdapter:
    name = os.environ.get("CHAMELEON_OLLAMA_MODEL")
    if name:
        return OllamaModel(name, os.environ.get("CHAMELEON_OLLAMA_HOST", "http://127.0.0.1:11434"))
    return NoModel()

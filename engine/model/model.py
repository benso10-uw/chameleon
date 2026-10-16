"""Model adapter: the one place every LLM call goes through.

The model choice waits on the organisers' approved list, so the backend is
picked by environment variable and can be swapped without touching the rest
of the engine:

- CHAMELEON_LLM_BASE_URL + CHAMELEON_LLM_MODEL (+ CHAMELEON_LLM_API_KEY,
  CHAMELEON_LLM_MAX_TOKENS)
                                 an OpenAI-compatible chat API, e.g. a hosted
                                 model for development (not usable in the
                                 offline scoring container)
- CHAMELEON_OLLAMA_MODEL=<name>  a local Ollama server (default host
  http://127.0.0.1:11434, override with CHAMELEON_OLLAMA_HOST)
- unset                          no model: every call raises ModelUnavailable,
                                 and the engine runs on its preset logic

Settings are read from the environment, then from a `.env` file at the repo
root (never committed; see `.env.example`). Real environment variables win.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ModelUnavailable(Exception):
    """No model can be reached; retrying won't help."""


class ModelAdapter(Protocol):
    def complete(self, prompt: str) -> str:
        """Return the model's reply to `prompt`. Raises ModelUnavailable if
        no model can be reached. Adapters may also set `last_call` to a dict
        of extra detail (model, reasoning, usage) for traces."""
        ...


class NoModel:
    def complete(self, prompt: str) -> str:
        raise ModelUnavailable("no model configured (see .env.example)")


class OllamaModel:
    """Deterministic JSON-mode completions from a local Ollama server."""

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 600):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout
        self.last_call = None

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
                reply = json.loads(response.read())
                self.last_call = {"model": self.model, "reasoning": reply.get("thinking"),
                                  "usage": {"prompt_tokens": reply.get("prompt_eval_count"),
                                            "completion_tokens": reply.get("eval_count")}}
                return reply["response"]
        except OSError as exc:
            raise ModelUnavailable(f"Ollama at {self.host} unreachable: {exc}") from exc


class OpenAICompatibleModel:
    """Deterministic chat completions from any OpenAI-compatible API."""

    def __init__(self, model: str, base_url: str, api_key: str = "", timeout: float = 600,
                 max_tokens: int | None = None, reasoning: str = "auto"):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key, self.timeout, self.max_tokens = api_key, timeout, max_tokens
        self.last_call = None
        self.reasoning = reasoning
        self._truncation_recovered = False

    def recover_truncation(self) -> bool:
        """One bounded recovery; never repeat a reasoning-starved request unchanged."""
        if self._truncation_recovered or "qwen" not in self.model.lower():
            return False
        self._truncation_recovered = True
        self.reasoning = "off"
        self.max_tokens = max(self.max_tokens or 0, 8192)
        return True

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "seed": 0,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if self.reasoning != "auto":
            if "qwen" in self.model.lower():
                payload["chat_template_kwargs"] = {"enable_thinking": self.reasoning != "off"}
            else:
                payload["reasoning_effort"] = "none" if self.reasoning == "off" else self.reasoning
        body = json.dumps(payload).encode()
        # Python's default User-Agent is blocked by some hosts' Cloudflare rules.
        headers = {"Content-Type": "application/json", "User-Agent": "chameleon-engine/0.1"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if self.api_key:
                detail = detail.replace(self.api_key, "[redacted]")
            if exc.code in (401, 403, 404, 408, 429, 502, 503, 504, 524):
                raise ModelUnavailable(f"Model request unavailable (HTTP {exc.code}); check credentials, quota or provider availability.") from None
            raise RuntimeError(f"{self.base_url} returned {exc.code}: {detail}") from exc  # retried
        except OSError as exc:
            raise ModelUnavailable(f"{self.base_url} unreachable: {exc}") from exc
        message = reply["choices"][0]["message"]
        content = message.get("content") or ""
        self.last_call = {"model": self.model,
                          "reasoning": message.get("reasoning") or message.get("reasoning_content"),
                          "usage": reply.get("usage"),
                          "finish_reason": reply["choices"][0].get("finish_reason")}
        # Reasoning models may inline their thinking; only the answer matters.
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load KEY=VALUE lines from `path` into the environment, without
    overriding variables that are already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def default_model() -> ModelAdapter:
    load_env_file()
    base_url, model = os.environ.get("CHAMELEON_LLM_BASE_URL"), os.environ.get("CHAMELEON_LLM_MODEL")
    if base_url and model:
        max_tokens = os.environ.get("CHAMELEON_LLM_MAX_TOKENS")
        return OpenAICompatibleModel(model, base_url, os.environ.get("CHAMELEON_LLM_API_KEY", ""),
                                     max_tokens=int(max_tokens) if max_tokens else None)
    name = os.environ.get("CHAMELEON_OLLAMA_MODEL")
    if name:
        return OllamaModel(name, os.environ.get("CHAMELEON_OLLAMA_HOST", "http://127.0.0.1:11434"))
    return NoModel()

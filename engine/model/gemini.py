"""Native Gemini adapter, using explicit thinking controls and JSON output."""

import json
import urllib.error
import urllib.request
from urllib.parse import quote

from engine.model.model import IMAGE_REJECTION_CODES, ImagesUnsupported, ModelUnavailable, png_b64

API = "https://generativelanguage.googleapis.com/v1beta"


def google_json(path, api_key, payload=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(API + path, data=data, headers={
        "x-goog-api-key": api_key, "Content-Type": "application/json",
        "User-Agent": "chameleon-workbench/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Do not log provider bodies, URLs with credentials, or the API key.
        labels = {400: "Invalid API key or unsupported model settings", 401: "Invalid API key",
                  403: "API key lacks access", 404: "Model not available",
                  429: "Quota exhausted or rate limited; wait before retrying"}
        error = ModelUnavailable(f"Gemini HTTP {exc.code}: {labels.get(exc.code, 'Provider request failed')}")
        error.status = exc.code
        raise error from None
    except OSError:
        raise ModelUnavailable("Cannot reach Gemini (connection failed or request timed out)") from None


def list_models(api_key):
    names, token = [], ""
    while True:
        data = google_json("/models?pageSize=1000" + ("&pageToken=" + quote(token, safe="") if token else ""), api_key)
        names.extend(m["name"].removeprefix("models/") for m in data.get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", []))
        token = data.get("nextPageToken")
        if not token:
            return sorted(names)


def thinking_config(model, reasoning):
    if reasoning == "auto":
        return {"includeThoughts": True}
    if "2.5" in model:
        if reasoning == "minimal":
            raise ValueError("Gemini 2.5 uses budgets: choose off, low, high or provider default.")
        if "pro" in model and reasoning == "off":
            raise ValueError("Gemini 2.5 Pro cannot disable thinking; choose low or auto.")
        return {"thinkingBudget": {"off": 0, "low": 1024, "high": 8192}[reasoning], "includeThoughts": True}
    if reasoning == "minimal":
        if not any(token in model for token in ("3.5-flash-lite", "3.1-flash-lite", "3.6-flash", "3-flash")):
            raise ValueError("Minimal is supported here for Gemini 3.5/3.1 Flash-Lite, 3.6 Flash and 3 Flash. Choose low for this model.")
    if reasoning == "off":
        # Avoid silently claiming reasoning is off on models that cannot disable it.
        raise ValueError("Off is supported here for Gemini 2.5 Flash/Flash-Lite. For newer models choose low or auto.")
    return {"thinkingLevel": reasoning.upper(), "includeThoughts": True}


class GeminiModel:
    accepts_images = True

    def __init__(self, model, api_key, reasoning="low", max_tokens=4096, timeout=90):
        self.model, self.api_key = model.removeprefix("models/"), api_key
        self.reasoning, self.max_tokens, self.timeout = reasoning, max_tokens, timeout
        self.last_call = None
        self.thinking = thinking_config(self.model, reasoning)

    def complete(self, prompt, images=None):
        config = {"temperature": 0, "maxOutputTokens": self.max_tokens,
                  "responseMimeType": "application/json"}
        if self.thinking:
            config["thinkingConfig"] = self.thinking
        request_parts = [{"text": prompt}] + [
            {"inline_data": {"mime_type": "image/png", "data": png_b64(image)}} for image in images or ()]
        try:
            reply = google_json("/models/" + quote(self.model, safe="-") + ":generateContent",
                                self.api_key, {"contents": [{"role": "user", "parts": request_parts}],
                                               "generationConfig": config}, self.timeout)
        except ModelUnavailable as exc:
            if images and getattr(exc, "status", None) in IMAGE_REJECTION_CODES:
                raise ImagesUnsupported(f"{self.model} rejected the image ({exc})") from None
            raise
        candidate = (reply.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        usage = reply.get("usageMetadata", {})
        self.last_call = {"model": self.model, "finish_reason": candidate.get("finishReason"),
                          "reasoning": "\n".join(p.get("text", "") for p in parts if p.get("thought")),
                          "usage": {"prompt_tokens": usage.get("promptTokenCount", 0),
                                    "completion_tokens": usage.get("candidatesTokenCount", 0),
                                    "total_tokens": usage.get("totalTokenCount", 0),
                                    "completion_tokens_details": {"reasoning_tokens": usage.get("thoughtsTokenCount", 0)}}}
        return "\n".join(p.get("text", "") for p in parts if not p.get("thought"))

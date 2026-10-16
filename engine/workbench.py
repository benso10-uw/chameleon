"""Local design workbench. Run: python -m engine.workbench (then open localhost:8765)."""

import argparse
import copy
import hashlib
import json
import math
import mimetypes
import os
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from engine.model.gemini import GeminiModel, list_models
from engine.model.model import NoModel, OllamaModel, OpenAICompatibleModel, load_env_file
from engine.resize import resize
from engine.tracing.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web"
OUTPUT = ROOT / "output" / "workbench"
ASSETS = ROOT / "brand_kit" / "assets"
RENDER_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
RUNS = {}
TASKS = {}


class Cancelled(Exception):
    pass


def validate_task(task):
    """Bound local imports before passing them to the renderer or a model."""
    if not isinstance(task, dict) or not isinstance(task.get("source"), dict):
        raise ValueError("Choose a task JSON containing source and target_canvas. A flat PNG has no editable elements.")
    for canvas in (task["source"].get("canvas"), task.get("target_canvas")):
        if not isinstance(canvas, dict) or not all(
                isinstance(canvas.get(k), (int, float)) and not isinstance(canvas[k], bool)
                and math.isfinite(canvas[k]) and 1 <= canvas[k] <= 8192 for k in ("width", "height")):
            raise ValueError("Canvas width and height must be between 1 and 8192 pixels.")
        if canvas["width"] * canvas["height"] > 20_000_000:
            raise ValueError("Canvas exceeds the 20 megapixel limit.")
    elements = task["source"].get("elements")
    if not isinstance(elements, list) or not 1 <= len(elements) <= 200:
        raise ValueError("A task must contain 1–200 elements.")
    ids = set()
    for el in elements:
        if not isinstance(el, dict) or not isinstance(el.get("id"), str) or el["id"] in ids:
            raise ValueError("Every element needs a unique string id.")
        ids.add(el["id"])
        if el.get("type") not in ("text", "image", "shape"):
            raise ValueError("Element types must be text, image, or shape.")
        for k in ("x", "y", "width", "height", "rotation", "font_size"):
            if k not in el and k not in ("rotation", "font_size"):
                raise ValueError(f"Element {el['id']} is missing {k}.")
            if k in el and (not isinstance(el[k], (int, float)) or isinstance(el[k], bool)
                            or not math.isfinite(el[k]) or abs(el[k]) > 16384):
                raise ValueError(f"Element {el['id']} has invalid {k}.")
        if el["width"] <= 0 or el["height"] <= 0 or el["width"] * el["height"] > 20_000_000:
            raise ValueError("Element dimensions must be positive and below 20 megapixels.")
        if el["type"] == "text" and (not isinstance(el.get("content"), str)
                or len(el["content"]) > 20000 or not isinstance(el.get("font_family"), str)
                or not isinstance(el.get("color"), str) or el.get("font_size", 0) <= 0):
            raise ValueError("Text elements need content, font_family, color and a positive font_size.")
        if el["type"] == "shape" and not all(isinstance(el.get(k), str) for k in ("shape_type", "fill_color")):
            raise ValueError("Shapes need shape_type and fill_color.")
        if "group_id" in el and not isinstance(el["group_id"], str):
            raise ValueError("group_id must be text.")
        if el["type"] == "image":
            asset = el.get("asset_id", "")
            if not isinstance(asset, str) or not asset or not (ASSETS / asset).resolve().is_relative_to(ASSETS.resolve()):
                raise ValueError("Image assets must be paths inside brand_kit/assets.")
    return task


def warnings(task):
    return [f"Missing asset: {e['asset_id']} (placeholder shown)" for e in task["source"]["elements"]
            if e["type"] == "image" and not (ASSETS / e["asset_id"]).is_file()]


def render(design, path):
    from renderer.converter.convert import render_design_to_png
    path.parent.mkdir(parents=True, exist_ok=True)
    with RENDER_LOCK:
        return render_design_to_png(design, ASSETS, path)


def task_id(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()[:16]


def source_image(task):
    key = task_id(task)
    path = OUTPUT / "sources" / f"{key}.png"
    if not path.exists():
        render(task["source"], path)
    return "/media/workbench/sources/" + path.name


def make_model(config):
    provider = config.get("provider", "preset")
    name = str(config.get("model", ""))[:150]
    reasoning = config.get("reasoning", "low")
    if reasoning not in ("auto", "off", "minimal", "low", "high"):
        raise ValueError("Choose a valid reasoning setting.")
    max_tokens = int(config.get("max_tokens", 4096))
    timeout = int(config.get("timeout", 90))
    if not 512 <= max_tokens <= 32768 or not 10 <= timeout <= 180:
        raise ValueError("Token cap must be 512–32768; timeout must be 10–180 seconds.")
    if provider == "preset":
        return NoModel()
    if not name.strip():
        raise ValueError("Enter a model name or refresh the model list.")
    key = config.get("api_key") or os.environ.get("GEMINI_API_KEY" if provider == "gemini" else "CHAMELEON_LLM_API_KEY", "")
    if provider == "gemini":
        if not key:
            raise ValueError("Enter a Gemini API key.")
        return GeminiModel(name, key, reasoning, max_tokens, timeout)
    url = str(config.get("base_url", "")).strip()
    if provider == "ollama":
        return OllamaModel(name, url or "http://127.0.0.1:11434", timeout=timeout)
    if provider == "openai":
        if reasoning == "minimal":
            raise ValueError("Minimal is a Gemini setting. Choose low or provider default for this API.")
        if not urlsplit(url).hostname or urlsplit(url).scheme not in ("http", "https") or urlsplit(url).username:
            raise ValueError("Enter an http(s) API base URL without embedded credentials.")
        return OpenAICompatibleModel(name, url, key, timeout=timeout, max_tokens=max_tokens, reasoning=reasoning)
    raise ValueError("Unknown provider.")


class Run:
    def __init__(self, task, name, model, config):
        self.id = uuid.uuid4().hex[:12]
        self.task, self.model = copy.deepcopy(task), model
        self.folder = OUTPUT / self.id
        self.folder.mkdir(parents=True)
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.data = {"id": self.id, "name": name, "state": "running", "created": time.time(),
                     "model": getattr(model, "model", "Preset only"), "reasoning": config.get("reasoning", "auto"),
                     "task": self.task, "steps": [], "images": {}, "warnings": warnings(task), "error": None}
        self.options = {"use_model_understanding": bool(config.get("model_understanding", False)),
                        "refine": bool(config.get("refine", True)) and not isinstance(model, NoModel),
                        "llm_tiebreak": bool(config.get("llm_tiebreak", False)),
                        "max_attempts": int(config.get("attempts", 2))}
        if not 1 <= self.options["max_attempts"] <= 3:
            raise ValueError("Choose 1–3 design attempts.")

    def on_step(self, step):
        if self.cancel.is_set():
            raise Cancelled()
        with self.lock:
            idx = len(self.data["steps"])
            self.data["steps"].append(copy.deepcopy(step))
        if step.get("layout"):
            try:
                render(step["layout"], self.folder / f"step{idx}.png")
                with self.lock:
                    self.data["images"][f"step{idx}"] = f"/media/workbench/{self.id}/step{idx}.png"
            except Exception as exc:
                with self.lock:
                    self.data["warnings"].append(f"Preview unavailable: {type(exc).__name__}: {exc}")

    def work(self):
        trace = Trace(self.data["name"], self.on_step)
        try:
            self.data["images"]["source"] = source_image(self.task)
            out = resize(self.task["source"], self.task["target_canvas"], model=self.model,
                         trace=trace, **self.options)
            (self.folder / "output.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
            self.data["state"] = "done"
        except Cancelled:
            self.data["state"] = "cancelled"
        except Exception as exc:
            self.data["state"], self.data["error"] = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            trace.save(self.folder / "trace.json")
            self.data["seconds"] = round(time.time() - self.data["created"], 2)
            (self.folder / "run.json").write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            self.model = None  # Drop credentials with the adapter, never write them to a run.

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)


def history():
    items = []
    for path in OUTPUT.glob("*/run.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append({k: data.get(k) for k in ("id", "name", "model", "state", "created", "seconds", "reasoning")})
        except (ValueError, OSError):
            continue
    for path in (ROOT / "output" / "traces").glob("*.json"):
        items.append({"id": "legacy-" + path.stem, "name": path.stem, "model": "Saved trace", "state": "done", "created": path.stat().st_mtime})
    return sorted(items, key=lambda item: item.get("created", 0), reverse=True)[:100]


def get_run(run_id):
    if run_id in RUNS:
        return RUNS[run_id].snapshot()
    if run_id.startswith("legacy-"):
        stem = run_id.removeprefix("legacy-")
        if not stem or Path(stem).name != stem or "/" in stem or "\\" in stem:
            raise ValueError("Invalid trace id.")
        trace = json.loads((ROOT / "output" / "traces" / f"{stem}.json").read_text(encoding="utf-8"))
        task = json.loads((ROOT / "input" / f"{stem}.json").read_text(encoding="utf-8"))
        images = {p.stem: f"/media/traces/{stem}/{p.name}" for p in (ROOT / "output" / "traces" / stem).glob("*.png")}
        return {"id": run_id, "name": stem, "state": "done", "model": "Saved trace", "task": task,
                "steps": trace["steps"], "images": images, "warnings": warnings(task), "error": None}
    if len(run_id) != 12 or not all(c in "0123456789abcdef" for c in run_id):
        raise ValueError("Invalid run id.")
    return json.loads((OUTPUT / run_id / "run.json").read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Query strings or credentials should never reach console logs.

    def respond(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return host in allowed and (not origin or origin in {"http://" + value for value in allowed})

    def do_GET(self):
        if not self.local_request():
            return self.respond({"error": "Local requests only."}, 403)
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/config":
                tasks = [{"id": key, "name": item["name"], "canvas": item["task"]["target_canvas"]} for key, item in TASKS.items()]
                return self.respond({"tasks": tasks, "history": history(),
                                     "gemini_key_saved": bool(os.environ.get("GEMINI_API_KEY")),
                                     "hosted_key_saved": bool(os.environ.get("CHAMELEON_LLM_API_KEY")),
                                     "hosted_model": os.environ.get("CHAMELEON_LLM_MODEL", ""),
                                     "hosted_url": os.environ.get("CHAMELEON_LLM_BASE_URL", ""),
                                     "renderer": renderer_ready()})
            if path.startswith("/api/tasks/"):
                item = TASKS[path.rsplit("/", 1)[-1]]
                return self.respond({**item, "source_image": source_image(item["task"]), "warnings": warnings(item["task"])})
            if path.startswith("/api/runs/"):
                data = get_run(path.rsplit("/", 1)[-1])
                after = max(0, int(parse_qs(parsed.query).get("after", [0])[0]))
                data["step_offset"] = after
                data["steps"] = data["steps"][after:]
                return self.respond(data)
            if path == "/api/history":
                return self.respond(history())
            if path.startswith("/media/"):
                root, relative = ROOT / "output", path.removeprefix("/media/")
                file = (root / relative).resolve()
                if not file.is_relative_to(root.resolve()) or file.suffix not in (".png", ".json"):
                    return self.respond({"error": "File unavailable."}, 404)
            else:
                file = STATIC / {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}.get(path, "not-found")
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        except (KeyError, FileNotFoundError):
            self.respond({"error": "Task or run not found."}, 404)
        except (ValueError, TypeError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Exception as exc:
            self.respond({"error": f"Preview unavailable ({type(exc).__name__}). Check that requirements.txt installed successfully."}, 500)

    def do_POST(self):
        if not self.local_request():
            return self.respond({"error": "Local requests only."}, 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 2_000_000:
                raise ValueError("Request must be below 2 MB.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected an object.")
            if self.path == "/api/import":
                task = validate_task(data.get("task"))
                key = task_id(task)
                TASKS[key] = {"name": str(data.get("name", "Imported task"))[:120], "task": task}
                return self.respond({"id": key})
            if self.path == "/api/models":
                key = data.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
                if not key:
                    raise ValueError("Enter a Gemini API key first.")
                return self.respond({"models": list_models(key)})
            if self.path == "/api/runs":
                item = TASKS[data["task_id"]]
                task = copy.deepcopy(item["task"])
                task["target_canvas"] = data.get("target", task["target_canvas"])
                validate_task(task)
                model = make_model(data)
                with RUN_LOCK:
                    if any(run.data["state"] == "running" for run in RUNS.values()):
                        return self.respond({"error": "A run is already active. Wait or stop it first."}, 409)
                    run = Run(task, item["name"], model, data)
                    RUNS[run.id] = run
                    threading.Thread(target=run.work, daemon=True).start()
                return self.respond({"id": run.id}, 202)
            if self.path.startswith("/api/cancel/"):
                run = RUNS[self.path.rsplit("/", 1)[-1]]
                run.cancel.set()
                return self.respond({"message": "Stopping after the current request finishes."})
            return self.respond({"error": "Unknown action."}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.respond({"error": str(exc)}, 400)
        except Exception as exc:
            # Native Gemini errors are sanitized; generic errors omit provider bodies.
            from engine.model.model import ModelUnavailable
            message = str(exc) if isinstance(exc, ModelUnavailable) else f"Request failed ({type(exc).__name__})."
            self.respond({"error": message}, 502)


def renderer_ready():
    try:
        from renderer.converter.convert import render_design_to_png
        return True
    except ImportError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    load_env_file(ROOT / ".env")
    for path in sorted((ROOT / "input").glob("*.json")):
        task = validate_task(json.loads(path.read_text(encoding="utf-8-sig")))
        TASKS[task_id(task)] = {"name": path.stem, "task": task}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Chameleon workbench: http://127.0.0.1:{args.port}", flush=True)
    if args.open_browser:
        threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

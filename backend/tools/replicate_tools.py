"""Replicate tools — let Mai run AI models hosted on Replicate.

Platform-provided (one shared token, available to every tenant). The agent
CHOOSES its own model for every task — nothing is pinned. Tools:
- search_models: discover a model for a task (image gen/edit, transcription, upscaling, TTS, video…).
- get_model_schema: see a model's exact input parameters before running it.
- run_model: run ANY Replicate model with a JSON of inputs (no filtering).

Outputs are returned as URLs/strings the agent can share with the user.
"""

from __future__ import annotations

import json
from typing import Any

from agno.tools import Toolkit

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _stringify(out: Any) -> str:
    """Turn a Replicate output (FileOutput / list / dict / str) into shareable text."""
    if out is None:
        return ""
    if hasattr(out, "url"):  # replicate FileOutput
        try:
            return str(out.url)
        except Exception:
            return str(out)
    if isinstance(out, dict):
        return json.dumps({k: _stringify(v) for k, v in out.items()})
    if isinstance(out, (list, tuple)):
        return ", ".join(_stringify(o) for o in out if o is not None)
    return str(out)


class ReplicateTools(Toolkit):
    def __init__(self) -> None:
        self.api_token = settings.replicate_api_token
        super().__init__(
            name="replicate_tools",
            tools=[self.run_model, self.search_models, self.get_model_schema],
            instructions=(
                "Run AI models on Replicate — YOU choose the model for the task; nothing is "
                "pinned. Workflow: search_models to find a suitable model, get_model_schema to "
                "see its exact input fields, then run_model with its 'owner/name' and a JSON of "
                "inputs (you may pass any of its parameters). For a NEW image from text, pick a "
                "text-to-image model; to EDIT a real photo while keeping people recognizable, "
                "pick an instruction-based image-EDIT model. Outputs come back as URLs — share "
                "them. Image/video gen can take a while; tell the user you're working on it."
            ),
            add_instructions=True,
        )

    def _client(self):
        import replicate

        return replicate.Client(api_token=self.api_token)

    # -- run any model ------------------------------------------------------
    def run_model(self, model: str, inputs: dict | str | None = None) -> str:
        """Run any Replicate model and return its output.

        Args:
            model: model ref, "owner/name" (latest) or "owner/name:version".
            inputs: the model's inputs as an object, e.g. {"prompt": "hello",
                "num_outputs": 1}. Use search_models first if unsure of the fields.
        """
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs) if inputs.strip() else {}
            except json.JSONDecodeError:
                return "inputs must be a JSON object, e.g. {\"prompt\": \"hi\"}."
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict):
            return "inputs must be a JSON object, e.g. {\"prompt\": \"hi\"}."
        try:
            from backend.replicate_util import pin_version

            client = self._client()
            out = client.run(pin_version(client, model), input=inputs)
        except Exception as exc:
            logger.warning("replicate.run_failed", extra={"ctx_error": str(exc), "ctx_model": model})
            return f"Model run failed: {exc}"
        return _stringify(out) or "(no output returned)"

    def get_model_schema(self, model: str) -> str:
        """Return a model's INPUT parameters so the caller can drive every knob.

        Args:
            model: "owner/name" (or "owner/name:version").
        Returns a JSON object {model, inputs:{param:{type, default, required,
        description}}} — exactly the fields run_model accepts for that model.
        """
        try:
            client = self._client()
            ref = model.split(":")[0]
            m = client.models.get(ref)
            ver = getattr(m, "latest_version", None)
            schema = getattr(ver, "openapi_schema", None) or {}
            inp = schema.get("components", {}).get("schemas", {}).get("Input", {})
            props = inp.get("properties", {})
            required = set(inp.get("required", []))
        except Exception as exc:
            logger.warning("replicate.schema_failed",
                           extra={"ctx_error": str(exc), "ctx_model": model})
            return f"Couldn't fetch schema for {model}: {exc}"
        if not props:
            return f"No input schema available for {model}."
        params = {}
        for name, spec in sorted(props.items(), key=lambda kv: kv[1].get("x-order", 999)):
            params[name] = {
                "type": spec.get("type") or ("enum" if spec.get("enum") else "any"),
                "default": spec.get("default"),
                "required": name in required,
                "description": (spec.get("description") or "")[:200],
                **({"options": spec["enum"]} if spec.get("enum") else {}),
            }
        return json.dumps({"model": model.split(":")[0], "inputs": params}, ensure_ascii=False)

    # -- discover models ----------------------------------------------------
    def search_models(self, query: str) -> str:
        """Search Replicate's public models for a task, to find what to run.

        Args:
            query: a description, e.g. "transcribe audio", "remove background",
                   "image upscaler", "text to speech".
        """
        try:
            page = self._client().models.search(query)
        except Exception as exc:
            logger.warning("replicate.search_failed", extra={"ctx_error": str(exc)})
            return f"Search failed: {exc}"
        items = list(page)[:8] if page is not None else []
        if not items:
            return "No models found for that query."
        lines = []
        for m in items:
            ref = f"{getattr(m, 'owner', '')}/{getattr(m, 'name', '')}".strip("/")
            desc = (getattr(m, "description", "") or "").strip().replace("\n", " ")[:120]
            lines.append(f"- {ref}: {desc}" if desc else f"- {ref}")
        return "Found these models (use run_model with one of these refs):\n" + "\n".join(lines)

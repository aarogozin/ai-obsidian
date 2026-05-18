from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class OmlxClient:
    base_url: str
    api_key: str | None = None

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        return [item["id"] for item in payload.get("data", []) if "id" in item]

    def choose_model(self, requested: str | None = None) -> str:
        models = self.list_models()
        if requested:
            resolved = resolve_model_id(requested, models)
            return resolved or requested

        if not models:
            raise OmlxError("oMLX is reachable, but no models are available.")
        return models[0]

    def chat(self, model: str, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        response = self._request("POST", "/chat/completions", payload)
        choices = response.get("choices", [])
        if not choices:
            raise OmlxError("oMLX returned no chat choices.")

        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            raise OmlxError("oMLX returned a response without text content.")
        return content.strip()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        body = None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401:
                raise OmlxError(
                    "oMLX requires an API key. Pass --api-key or set OMLX_API_KEY."
                ) from exc
            raise OmlxError(f"oMLX HTTP error at {url}: {exc.code} {exc.reason}") from exc
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OmlxError(f"Could not reach oMLX at {url}: {exc}") from exc


class OmlxError(RuntimeError):
    pass


def resolve_model_id(requested: str, available: list[str]) -> str | None:
    requested_tail = requested.rsplit("/", maxsplit=1)[-1]
    for model in available:
        model_tail = model.rsplit("/", maxsplit=1)[-1]
        if requested == model or requested_tail == model_tail:
            return model
    return None

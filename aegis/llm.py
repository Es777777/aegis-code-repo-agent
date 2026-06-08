from __future__ import annotations

import json
import urllib.error
import urllib.request

from aegis.config import LLMConfig


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @property
    def available(self) -> bool:
        return bool(self.config.enabled and self.config.api_key)

    def complete(self, *, system: str, user: str) -> str:
        if not self.available:
            raise LLMError("LLM is not enabled or API key is missing.")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError("LLM response is not JSON.") from exc
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response does not contain a chat completion.") from exc

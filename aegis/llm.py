from __future__ import annotations

import json
import urllib.error
import urllib.request

from aegis.config import LLMConfig


class LLMError(RuntimeError):
    pass


class LLMClient:
    MAX_ERROR_BODY_CHARS = 800

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
        try:
            request = urllib.request.Request(
                f"{self.config.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except ValueError as exc:
            raise LLMError(f"LLM request URL is invalid: {self.config.base_url}") from exc
        except urllib.error.HTTPError as exc:
            body = self._error_body(exc)
            detail = f" HTTP response body: {body}" if body else ""
            raise LLMError(f"LLM request failed with HTTP {exc.code} {exc.reason}.{detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        except TimeoutError as exc:
            raise LLMError(f"LLM request timed out after {self.config.timeout_seconds} seconds.") from exc
        except json.JSONDecodeError as exc:
            raise LLMError("LLM response is not JSON.") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response does not contain a chat completion.") from exc
        text = self._message_content_text(content)
        if not text:
            raise LLMError("LLM response chat completion is empty.")
        return text

    @classmethod
    def _message_content_text(cls, content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts).strip()
        return ""

    @classmethod
    def _error_body(cls, exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read()
        except OSError:
            return ""
        if not raw:
            return ""
        text = raw.decode("utf-8", errors="replace").strip()
        if len(text) > cls.MAX_ERROR_BODY_CHARS:
            return text[: cls.MAX_ERROR_BODY_CHARS].rstrip() + "...[truncated]"
        return text

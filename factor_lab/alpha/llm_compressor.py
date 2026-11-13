"""Optional DeepSeek Flash compressor for long research-memory contexts.

The compressor is a callable injected into ``ResearchMemory.build_context``.
It reads the API key from the gitignored ``config.json`` (or explicit
environment variables), never from source code, and returns a JSON object with
``source_campaign_ids`` covering every input campaign.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class CompressorError(RuntimeError):
    pass


def _repo_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config.json"


def _load_llm_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else _repo_config_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    llm = payload.get("llm") if isinstance(payload, dict) else {}
    return dict((llm or {}).get("deepseek") or {})


class DeepSeekCompressor:
    """OpenAI-compatible DeepSeek Flash compressor with source-id validation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        config = _load_llm_config(config_path)
        self.api_key = str(api_key or config.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or "")
        self.base_url = str(base_url or config.get("base_url") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.model = str(model or config.get("model") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash")
        self.timeout_seconds = int(timeout_seconds or config.get("timeout_seconds") or 120)
        self.max_retries = int(max_retries if max_retries is not None else config.get("max_retries") or 2)
        if not self.api_key:
            raise CompressorError(
                "DeepSeek API key missing: put llm.deepseek.api_key in config.json "
                "(gitignored) or set DEEPSEEK_API_KEY"
            )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.0 + attempt)
        raise CompressorError(f"DeepSeek request failed: {type(last_error).__name__}") from last_error

    @staticmethod
    def _parse_content(body: dict[str, Any]) -> dict[str, Any]:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CompressorError("DeepSeek response has no message content") from exc
        text = str(content).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CompressorError(f"DeepSeek content is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CompressorError("DeepSeek content must be a JSON object")
        return parsed

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        campaigns = payload.get("campaigns") or []
        hard = payload.get("hard_negatives") or []
        expected_ids = [str(row.get("campaign_id")) for row in campaigns]
        system = (
            "You compress a quantitative research-memory history. Use only the supplied facts. "
            "Never invent numbers or verdicts. Preserve every hard negative exactly. "
            "Do not nominate a best, strongest, peak or winner; report facts, not rankings. "
            "Every numeric claim must be copied from the input without rounding or comparison. "
            "Return JSON with keys: summary, key_lessons, hard_negatives, source_campaign_ids, open_questions. "
            "source_campaign_ids must contain every campaign id in the input."
        )
        user = json.dumps({"campaigns": campaigns, "hard_negatives": hard}, ensure_ascii=False, default=str)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        parsed = self._parse_content(self._post(body))
        returned = {str(item) for item in (parsed.get("source_campaign_ids") or [])}
        missing = sorted(set(expected_ids) - returned)
        if missing:
            # One strict retry; no silent source-id fabrication.
            retry_system = system + " The previous answer missed campaign ids: " + ", ".join(missing) + ". Return all of them."
            body["messages"][0]["content"] = retry_system
            parsed = self._parse_content(self._post(body))
            returned = {str(item) for item in (parsed.get("source_campaign_ids") or [])}
            missing = sorted(set(expected_ids) - returned)
        if missing:
            raise CompressorError(f"DeepSeek compression omitted source campaigns: {missing}")
        return parsed


__all__ = ["CompressorError", "DeepSeekCompressor"]

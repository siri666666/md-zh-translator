from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_int(value: Any, field_name: str, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数，收到: {value}") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name} 必须 >= {minimum}，收到: {parsed}")
    return parsed


def _parse_float(value: Any, field_name: str, minimum: float, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字，收到: {value}") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name} 必须 >= {minimum}，收到: {parsed}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} 必须 <= {maximum}，收到: {parsed}")
    return parsed


def _parse_optional_int(value: Any, field_name: str, minimum: int) -> int | None:
    candidate = _first_non_empty(value)
    if candidate is None:
        return None
    return _parse_int(candidate, field_name, minimum=minimum)


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    base_url: str
    model: str
    max_chars: int = 2600
    temperature: float = 0.2
    max_retries: int = 3
    timeout: float = 120.0
    max_rpm: int | None = None
    max_tpm: int | None = None

    @classmethod
    def from_args(cls, args: Any, require_api: bool = True) -> "AppConfig":
        api_key = _first_non_empty(
            getattr(args, "api_key", None),
            os.getenv("MDT_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
        )
        base_url = _first_non_empty(
            getattr(args, "base_url", None),
            os.getenv("MDT_BASE_URL"),
            os.getenv("OPENAI_BASE_URL"),
            "https://api.openai.com/v1",
        )
        model = _first_non_empty(
            getattr(args, "model", None),
            os.getenv("MDT_MODEL"),
            "gpt-4o-mini",
        )

        if require_api and not api_key:
            raise ValueError("缺少 API Key，请通过 --api-key 或环境变量 MDT_API_KEY 提供。")
        if not api_key:
            api_key = "DUMMY_KEY"

        max_chars = _parse_int(
            _first_non_empty(getattr(args, "max_chars", None), os.getenv("MDT_MAX_CHARS"), 2600),
            "max_chars",
            minimum=400,
        )
        temperature = _parse_float(
            _first_non_empty(getattr(args, "temperature", None), os.getenv("MDT_TEMPERATURE"), 0.2),
            "temperature",
            minimum=0.0,
            maximum=2.0,
        )
        max_retries = _parse_int(
            _first_non_empty(getattr(args, "max_retries", None), os.getenv("MDT_MAX_RETRIES"), 3),
            "max_retries",
            minimum=1,
        )
        timeout = _parse_float(
            _first_non_empty(getattr(args, "timeout", None), os.getenv("MDT_TIMEOUT"), 120),
            "timeout",
            minimum=1,
        )
        max_rpm = _parse_optional_int(
            _first_non_empty(getattr(args, "max_rpm", None), os.getenv("MDT_MAX_RPM")),
            "max_rpm",
            minimum=1,
        )
        max_tpm = _parse_optional_int(
            _first_non_empty(getattr(args, "max_tpm", None), os.getenv("MDT_MAX_TPM")),
            "max_tpm",
            minimum=1,
        )

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_chars=max_chars,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
            max_rpm=max_rpm,
            max_tpm=max_tpm,
        )

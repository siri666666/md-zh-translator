from __future__ import annotations

import re
import time

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from .config import AppConfig
from .rate_limiter import SlidingWindowRateLimiter

SYSTEM_PROMPT = """你是一个严谨的英文 Markdown 到简体中文翻译器。
请严格遵循以下规则：
1. 只翻译英文自然语言文本，不要翻译代码、命令、公式、URL、DOI、arXiv 编号、引文键。
2. Markdown 结构必须保持不变：标题层级、列表、表格、链接、图片、空行、分隔线都不能破坏。
3. 遇到占位符（形如 @@__MDTZ_...__@@）必须原样保留，不能改动任何字符。
4. 文献参考条目和引用格式不要翻译。
5. 若输入存在 PDF/OCR 断行，请在段落内重建通顺句子，不要把同一句拆成生硬短句。
6. 术语统一：ultrafast -> 超高速；high-precision -> 高精度；workspace -> 工作空间。
7. 不要输出解释、说明或额外前后缀，只输出翻译后的 Markdown 片段。
"""


class TranslationClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._limiter = None
        if config.max_rpm or config.max_tpm:
            self._limiter = SlidingWindowRateLimiter(
                max_rpm=config.max_rpm,
                max_tpm=config.max_tpm,
            )

    def translate_chunk(self, markdown_chunk: str) -> str:
        if not markdown_chunk.strip():
            return markdown_chunk

        estimated_total_tokens = self._estimate_total_tokens(markdown_chunk)
        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            if self._limiter is not None:
                self._limiter.acquire(estimated_tokens=estimated_total_tokens)
            try:
                response = self._client.chat.completions.create(
                    model=self._config.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": markdown_chunk},
                    ],
                    temperature=self._config.temperature,
                    timeout=self._config.timeout,
                )
                actual_total_tokens = self._extract_total_tokens(response)
                if self._limiter is not None and actual_total_tokens is not None:
                    self._limiter.add_positive_delta(actual_total_tokens - estimated_total_tokens)
                content = response.choices[0].message.content
                text = self._normalize_content(content)
                return self._apply_term_fixes(self._strip_code_fence_wrapper(text))
            except RateLimitError as exc:
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                retry_after = self._retry_after_seconds(exc)
                backoff = min(2**attempt, 10)
                time.sleep(max(backoff, retry_after))
            except (APITimeoutError, APIConnectionError, APIError) as exc:
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                time.sleep(min(2**attempt, 10))

        raise RuntimeError(f"翻译请求失败，已重试 {self._config.max_retries} 次。") from last_error

    @staticmethod
    def _normalize_content(content: object) -> str:
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    value = part.get("text")
                    if isinstance(value, str):
                        texts.append(value)
            return "".join(texts)
        return str(content)

    @staticmethod
    def _strip_code_fence_wrapper(text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return text

        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].startswith("```"):
            return "\n".join(lines[1:-1])
        return text

    @staticmethod
    def _apply_term_fixes(text: str) -> str:
        replacements = {
            "超快速": "超高速",
        }
        fixed = text
        for source, target in replacements.items():
            fixed = fixed.replace(source, target)
        return fixed

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Conservative approximation for mixed English/Chinese markdown.
        return max(1, (len(text.encode("utf-8")) + 2) // 3)

    def _estimate_total_tokens(self, markdown_chunk: str) -> int:
        prompt_tokens = self._estimate_tokens(SYSTEM_PROMPT) + self._estimate_tokens(markdown_chunk) + 64
        completion_tokens = max(64, int(self._estimate_tokens(markdown_chunk) * 1.2))
        return prompt_tokens + completion_tokens

    @staticmethod
    def _extract_total_tokens(response: object) -> int | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None

        total = getattr(usage, "total_tokens", None)
        if isinstance(total, int) and total > 0:
            return total

        if isinstance(usage, dict):
            total = usage.get("total_tokens")
            if isinstance(total, int) and total > 0:
                return total

            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if isinstance(prompt, int) and isinstance(completion, int):
                return prompt + completion
            return None

        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if isinstance(prompt, int) and isinstance(completion, int):
            return prompt + completion
        return None

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        if not headers:
            return 0.0

        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        for key in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
            parsed = TranslationClient._parse_duration_seconds(normalized.get(key))
            if parsed > 0:
                return parsed
        return 0.0

    @staticmethod
    def _parse_duration_seconds(raw: str | None) -> float:
        if not raw:
            return 0.0
        text = raw.strip().lower()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s)?", text)
        if not match:
            return 0.0
        value = float(match.group(1))
        unit = match.group(2) or "s"
        if unit == "ms":
            return value / 1000.0
        return value

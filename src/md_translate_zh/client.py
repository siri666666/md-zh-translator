from __future__ import annotations

from dataclasses import dataclass
import time

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from .config import AppConfig

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


@dataclass
class ChunkMetrics:
    attempts: int = 0
    retries: int = 0
    rate_limit_hits: int = 0
    backoff_sleep_s: float = 0.0
    request_elapsed_s: float = 0.0

    def merge(self, other: "ChunkMetrics") -> None:
        self.attempts += other.attempts
        self.retries += other.retries
        self.rate_limit_hits += other.rate_limit_hits
        self.backoff_sleep_s += other.backoff_sleep_s
        self.request_elapsed_s += other.request_elapsed_s


class RateLimitAbortError(RuntimeError):
    """Raised when the upstream provider responds with 429."""


class TranslationClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def translate_chunk(self, markdown_chunk: str) -> str:
        translated, _ = self.translate_chunk_with_metrics(markdown_chunk)
        return translated

    def translate_chunk_with_metrics(self, markdown_chunk: str) -> tuple[str, ChunkMetrics]:
        metrics = ChunkMetrics()
        if not markdown_chunk.strip():
            return markdown_chunk, metrics

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            metrics.attempts += 1
            request_started = time.monotonic()
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
                metrics.request_elapsed_s += max(0.0, time.monotonic() - request_started)
                content = response.choices[0].message.content
                text = self._normalize_content(content)
                return self._apply_term_fixes(self._strip_code_fence_wrapper(text)), metrics
            except RateLimitError as exc:
                metrics.request_elapsed_s += max(0.0, time.monotonic() - request_started)
                metrics.rate_limit_hits += 1
                raise RateLimitAbortError("命中 429，请降低 --concurrency 后重试（建议先减半）。") from exc
            except (APITimeoutError, APIConnectionError, APIError) as exc:
                metrics.request_elapsed_s += max(0.0, time.monotonic() - request_started)
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                sleep_seconds = min(2**attempt, 10)
                metrics.retries += 1
                metrics.backoff_sleep_s += sleep_seconds
                time.sleep(sleep_seconds)

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

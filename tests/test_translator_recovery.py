from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import RateLimitError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from md_translate_zh import cli
from md_translate_zh.client import ChunkMetrics, RateLimitAbortError, TranslationClient
from md_translate_zh.config import AppConfig
from md_translate_zh.translator import MarkdownTranslator

PLACEHOLDER_RE = re.compile(r"@@__MDTZ_[A-Z]+_\d{5}__@@")


class RecoveryClient:
    def translate_chunk_with_metrics(self, chunk: str) -> tuple[str, ChunkMetrics]:
        metrics = ChunkMetrics()
        placeholders = PLACEHOLDER_RE.findall(chunk)
        if "TRIGGER_RECOVERY" in chunk and len(chunk) > 400 and placeholders:
            broken = chunk.replace(placeholders[0], "", 1)
            return f"中文恢复中：{broken}", metrics
        return f"中文：{chunk}", metrics


class AlwaysDropPlaceholderClient:
    def translate_chunk_with_metrics(self, chunk: str) -> tuple[str, ChunkMetrics]:
        metrics = ChunkMetrics()
        if PLACEHOLDER_RE.search(chunk):
            broken = PLACEHOLDER_RE.sub("", chunk)
            return f"中文失败：{broken}", metrics
        return chunk, metrics


class EchoEnglishClient:
    def translate_chunk_with_metrics(self, chunk: str) -> tuple[str, ChunkMetrics]:
        return chunk, ChunkMetrics()


class PrefixClient:
    def translate_chunk_with_metrics(self, chunk: str) -> tuple[str, ChunkMetrics]:
        return f"[ZH]{chunk}", ChunkMetrics()


class PlaceholderArtifactClient:
    def translate_chunk_with_metrics(self, chunk: str) -> tuple[str, ChunkMetrics]:
        metrics = ChunkMetrics()
        placeholders = PLACEHOLDER_RE.findall(chunk)
        if placeholders:
            # 保留一个完整占位符，同时拼接残片前缀，模拟模型产生脏字符串。
            return f"中文 @@__MDTZ_INLINE_{placeholders[0]}", metrics
        return chunk, metrics


class TranslatorRecoveryTests(unittest.TestCase):
    def _build_recovery_markdown(self) -> str:
        return (
            "TRIGGER_RECOVERY The microDelta-0.5X could also follow the desired trajectories with high precision "
            "and accuracy over long runs, while maintaining stability in control loops and repeatability "
            "under fabrication variance. All trajectories were commanded at a $z$ height of $638\\mu \\mathrm{m}$ "
            "and scaled down to $\\sim 30~\\mu \\mathrm{m}$, except the asterisk trajectory, which was scaled to "
            "$20~\\mu \\mathrm{m}$ to reduce required actuation voltage and preserve electrostatic headroom. "
            "The maximum recorded RMS precision error was $0.6\\mu \\mathrm{m}$ and the maximum RMS accuracy error "
            "was $1.4\\mu \\mathrm{m}$ for scaled-down trajectories.\n"
        )

    def test_placeholder_subchunk_recovery_succeeds(self) -> None:
        translator = MarkdownTranslator(client=RecoveryClient(), max_chars=2600, concurrency=1)
        source = self._build_recovery_markdown()

        result = translator.translate(source)

        self.assertEqual(result.recovered_chunks, 1)
        self.assertEqual(result.hard_failed_chunks, 0)
        self.assertEqual(result.guard_fallback_chunks, 0)
        self.assertEqual(result.unresolved_placeholders, [])
        self.assertIn("中文", result.text)
        self.assertIn("$638\\mu \\mathrm{m}$", result.text)

    def test_persistent_placeholder_failure_marks_hard_failed(self) -> None:
        translator = MarkdownTranslator(client=AlwaysDropPlaceholderClient(), max_chars=2600, concurrency=1)
        source = self._build_recovery_markdown()

        result = translator.translate(source)

        self.assertEqual(result.hard_failed_chunks, 1)
        self.assertEqual(result.guard_fallback_chunks, 1)
        self.assertGreaterEqual(result.suspicious_unchanged_chunks, 1)
        self.assertIn("TRIGGER_RECOVERY The microDelta-0.5X", result.text)

    def test_suspicious_untranslated_chunk_detected(self) -> None:
        translator = MarkdownTranslator(client=EchoEnglishClient(), max_chars=2600, concurrency=1)
        source = (
            "This paragraph should normally be translated to Chinese but is intentionally echoed back by the "
            "mock model so we can validate suspicious unchanged chunk detection. It contains enough words and "
            "length to pass heuristic thresholds for unchanged English output in strict mode.\n"
        )

        result = translator.translate(source)

        self.assertEqual(result.hard_failed_chunks, 0)
        self.assertGreaterEqual(result.suspicious_unchanged_chunks, 1)

    def test_placeholder_artifact_is_treated_as_failure(self) -> None:
        translator = MarkdownTranslator(client=PlaceholderArtifactClient(), max_chars=2600, concurrency=1)
        source = self._build_recovery_markdown()

        result = translator.translate(source)

        self.assertEqual(result.hard_failed_chunks, 1)
        self.assertEqual(result.guard_fallback_chunks, 1)

    def test_reference_section_is_skipped_by_default(self) -> None:
        translator = MarkdownTranslator(client=PrefixClient(), max_chars=2600, concurrency=1)
        source = (
            "# Intro\n\n"
            "This is a plain paragraph that should be translated.\n\n"
            "# REFERENCES AND NOTES\n\n"
            "1. A. Example reference title.\n\n"
            "# ScienceRobotics\n\n"
            "Supplement text.\n"
        )

        result = translator.translate(source)

        self.assertIn("[ZH]This is a plain paragraph", result.text)
        self.assertIn("1. A. Example reference title.", result.text)
        self.assertNotIn("[ZH]1. A. Example reference title.", result.text)

    def test_repair_does_not_reorder_existing_placeholders(self) -> None:
        source = (
            "All trajectories were commanded at @@__MDTZ_INLINE_00089__@@ height of "
            "@@__MDTZ_INLINE_00090__@@ and scaled down to @@__MDTZ_INLINE_00091__@@.\n"
        )
        translated_missing_one = (
            "所有轨迹在 @@__MDTZ_INLINE_00089__@@ 高度下执行，并缩小到 "
            "@@__MDTZ_INLINE_00091__@@。\n"
        )

        repaired = MarkdownTranslator._repair_placeholder_tokens(source, translated_missing_one)
        repaired_tokens = PLACEHOLDER_RE.findall(repaired)
        source_tokens = PLACEHOLDER_RE.findall(source)

        self.assertEqual(repaired_tokens, source_tokens)

    def test_cli_strict_failure_does_not_write_output(self) -> None:
        source = self._build_recovery_markdown()

        class _CliClient(AlwaysDropPlaceholderClient):
            def __init__(self, _config: object) -> None:
                super().__init__()

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "in.md"
            output_path = Path(tmp_dir) / "out.zh.md"
            input_path.write_text(source, encoding="utf-8")

            argv = [
                "md-zh-translator",
                "-i",
                str(input_path),
                "-o",
                str(output_path),
                "--api-key",
                "test-key",
                "--base-url",
                "https://example.com/v1",
                "--model",
                "fake-model",
                "--concurrency",
                "1",
                "--strict-integrity",
            ]

            with patch("md_translate_zh.cli.TranslationClient", _CliClient):
                with patch.object(sys, "argv", argv):
                    code = cli.main()

            self.assertEqual(code, 1)
            self.assertFalse(output_path.exists())

    def test_client_429_raises_rate_limit_abort_without_retry(self) -> None:
        class _FailingCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_: object) -> object:
                self.calls += 1
                request = httpx.Request("POST", "https://example.com/v1/chat/completions")
                response = httpx.Response(429, request=request, headers={"retry-after": "2"})
                raise RateLimitError("Too Many Requests", response=response, body={"error": "rate limit"})

        class _FakeChat:
            def __init__(self) -> None:
                self.completions = _FailingCompletions()

        class _FakeOpenAIClient:
            def __init__(self) -> None:
                self.chat = _FakeChat()

        config = AppConfig(
            api_key="test-key",
            base_url="https://example.com/v1",
            model="fake-model",
            max_retries=5,
            timeout=30.0,
            concurrency=1,
        )
        client = TranslationClient(config)
        fake = _FakeOpenAIClient()
        client._client = fake

        with self.assertRaises(RateLimitAbortError):
            client.translate_chunk_with_metrics("Translate this paragraph.")
        self.assertEqual(fake.chat.completions.calls, 1)


if __name__ == "__main__":
    unittest.main()

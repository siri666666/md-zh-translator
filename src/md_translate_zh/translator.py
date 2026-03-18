from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from .cleaner import normalize_ocr_line_breaks
from .client import TranslationClient
from .markdown_processor import MarkdownMasker

ProgressCallback = Optional[Callable[[int, int], None]]
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S")
PLACEHOLDER_LINE_PATTERN = re.compile(r"^@@__MDTZ_[A-Z]+_\d{5}__@@$")
PLACEHOLDER_PATTERN = re.compile(r"@@__MDTZ_[A-Z]+_\d{5}__@@")


@dataclass
class TranslationResult:
    text: str
    total_chunks: int
    translated_chunks: int
    protected_items: int
    merged_breaks: int
    guard_fallback_chunks: int
    unresolved_placeholders: List[str]


@dataclass
class Segment:
    text: str
    translatable: bool


class MarkdownTranslator:
    def __init__(
        self,
        client: TranslationClient,
        max_chars: int,
        concurrency: int = 1,
        skip_reference_sections: bool = True,
        skip_reference_lines: bool = True,
        normalize_ocr_breaks: bool = True,
    ) -> None:
        self.client = client
        self.max_chars = max_chars
        self.concurrency = max(1, concurrency)
        self.normalize_ocr_breaks = normalize_ocr_breaks
        self.masker = MarkdownMasker(
            skip_reference_sections=skip_reference_sections,
            skip_reference_lines=skip_reference_lines,
        )

    def translate(
        self,
        markdown_text: str,
        dry_run: bool = False,
        progress_callback: ProgressCallback = None,
    ) -> TranslationResult:
        masked = self.masker.mask(markdown_text)
        merged_breaks = 0
        masked_text = masked.masked_text
        if self.normalize_ocr_breaks:
            reflowed = normalize_ocr_line_breaks(masked_text)
            masked_text = reflowed.text
            merged_breaks = reflowed.merged_breaks

        segments = segment_markdown_for_translation(masked_text, self.max_chars)
        translatable_indexes = [idx for idx, segment in enumerate(segments) if segment.translatable]
        translated_parts: List[str] = [segment.text for segment in segments]
        translated_count = 0
        guard_fallback_chunks = 0
        completed = 0
        total = len(translatable_indexes)

        pending: List[tuple[int, str]] = []
        for idx in translatable_indexes:
            source_text = segments[idx].text
            if dry_run or self._should_skip_chunk(source_text):
                translated_parts[idx] = source_text
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total)
                continue
            pending.append((idx, source_text))

        if self.concurrency <= 1 or len(pending) <= 1:
            for idx, source_text in pending:
                translated_text, used_fallback = self._translate_segment_with_guard(source_text)
                translated_parts[idx] = self._preserve_line_ending_suffix(source_text, translated_text)
                guard_fallback_chunks += used_fallback
                translated_count += 1
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total)
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                futures = {
                    executor.submit(self._translate_segment_with_guard, source_text): (idx, source_text)
                    for idx, source_text in pending
                }
                for future in as_completed(futures):
                    idx, source_text = futures[future]
                    translated_text, used_fallback = future.result()
                    translated_parts[idx] = self._preserve_line_ending_suffix(source_text, translated_text)
                    guard_fallback_chunks += used_fallback
                    translated_count += 1
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total)

        merged = "".join(translated_parts)
        restored = MarkdownMasker.unmask(merged, masked.replacements)
        unresolved = MarkdownMasker.find_placeholders(restored)

        return TranslationResult(
            text=restored,
            total_chunks=total,
            translated_chunks=translated_count,
            protected_items=len(masked.replacements),
            merged_breaks=merged_breaks,
            guard_fallback_chunks=guard_fallback_chunks,
            unresolved_placeholders=unresolved,
        )

    @staticmethod
    def _should_skip_chunk(chunk: str) -> bool:
        if not chunk.strip():
            return True
        if not re.search(r"[A-Za-z]", chunk):
            return True
        if re.fullmatch(r"(?:\s*@@__MDTZ_[A-Z]+_\d{5}__@@\s*)+", chunk):
            return True
        return False

    @staticmethod
    def _preserve_line_ending_suffix(source: str, translated: str) -> str:
        match = re.search(r"((?:\r\n|\n|\r)+)$", source)
        if not match:
            return translated
        suffix = match.group(1)
        stripped = re.sub(r"(?:\r\n|\n|\r)+$", "", translated)
        return stripped + suffix

    def _translate_segment_with_guard(self, source_segment: str) -> tuple[str, int]:
        source_placeholders = self._placeholder_counter(source_segment)
        if not source_placeholders:
            return self.client.translate_chunk(source_segment), 0

        # 占位符丢失会直接导致公式/代码/URL 破坏，这里进行额外守护重试。
        for _ in range(3):
            translated = self.client.translate_chunk(source_segment)
            if self._placeholder_counter(translated) == source_placeholders:
                return translated, 0

        return source_segment, 1

    @staticmethod
    def _placeholder_counter(text: str) -> Counter[str]:
        return Counter(PLACEHOLDER_PATTERN.findall(text))


def segment_markdown_for_translation(text: str, max_chars: int) -> List[Segment]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [Segment(text=text, translatable=True)] if text else []

    segments: List[Segment] = []
    block_lines: List[str] = []

    def flush_block() -> None:
        if not block_lines:
            return
        block_text = "".join(block_lines)
        segments.extend(_split_translatable_block(block_text, max_chars))
        block_lines.clear()

    for line in lines:
        if _is_blank_line(line):
            flush_block()
            segments.append(Segment(text=line, translatable=False))
            continue

        if PLACEHOLDER_LINE_PATTERN.match(line.strip()):
            flush_block()
            segments.append(Segment(text=line, translatable=False))
            continue

        if HEADING_PATTERN.match(line):
            flush_block()
            segments.extend(_split_translatable_block(line, max_chars))
            continue

        block_lines.append(line)

    flush_block()
    return segments


def _split_translatable_block(text: str, max_chars: int) -> List[Segment]:
    if len(text) <= max_chars:
        return [Segment(text=text, translatable=True)]
    return [Segment(text=piece, translatable=True) for piece in _split_by_lines(text, max_chars)]


def _is_blank_line(line: str) -> bool:
    return not line.strip()


def _split_by_lines(text: str, max_chars: int) -> List[str]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [text]

    chunks: List[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) <= max_chars:
            current += line
            continue
        if current:
            chunks.append(current)
        if len(line) <= max_chars:
            current = line
            continue

        # 防止超长单行（例如很长 URL）导致无限循环
        start = 0
        while start < len(line):
            end = min(start + max_chars, len(line))
            chunks.append(line[start:end])
            start = end
        current = ""

    if current:
        chunks.append(current)
    return chunks

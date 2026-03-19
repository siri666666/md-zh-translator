from __future__ import annotations

from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from difflib import SequenceMatcher
import re
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

from .cleaner import normalize_ocr_line_breaks
from .client import ChunkMetrics, TranslationClient
from .markdown_processor import MarkdownMasker

ProgressCallback = Optional[Callable[[int, int], None]]
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S")
PLACEHOLDER_LINE_PATTERN = re.compile(r"^@@__MDTZ_[A-Z]+_\d{5}__@@$")
PLACEHOLDER_PATTERN = re.compile(r"@@__MDTZ_[A-Z]+_\d{5}__@@")
RECOVERY_ROUNDS = 2
RECOVERY_BASE_MAX_CHARS = 420
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?。！？;；])\s+")
WHITESPACE_PATTERN = re.compile(r"\s+")
LATIN_WORD_PATTERN = re.compile(r"[A-Za-z]{3,}")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
SUSPICIOUS_MIN_CHARS = 180
SUSPICIOUS_MIN_WORDS = 20
SUSPICIOUS_MAX_CJK = 2
SUSPICIOUS_SIMILARITY = 0.92


@dataclass
class TranslationResult:
    text: str
    total_chunks: int
    translated_chunks: int
    protected_items: int
    merged_breaks: int
    guard_fallback_chunks: int
    recovered_chunks: int
    hard_failed_chunks: int
    suspicious_unchanged_chunks: int
    unresolved_placeholders: List[str]


@dataclass
class Segment:
    text: str
    translatable: bool


@dataclass
class GuardedSegmentResult:
    text: str
    used_fallback: bool
    recovered: bool
    hard_failed: bool
    metrics: ChunkMetrics


class MarkdownTranslator:
    def __init__(
        self,
        client: TranslationClient,
        max_chars: int,
        concurrency: int | None = None,
        skip_reference_sections: bool = True,
        skip_reference_lines: bool = True,
        normalize_ocr_breaks: bool = True,
    ) -> None:
        self.client = client
        self.max_chars = max_chars
        self.concurrency = max(1, concurrency) if concurrency is not None else 1
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
        recovered_chunks = 0
        hard_failed_chunks = 0
        suspicious_unchanged_chunks = 0
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
                outcome = self._translate_segment_with_guard(source_text)
                translated_text = self._preserve_line_ending_suffix(source_text, outcome.text)
                translated_parts[idx] = translated_text
                guard_fallback_chunks += int(outcome.used_fallback)
                recovered_chunks += int(outcome.recovered)
                hard_failed_chunks += int(outcome.hard_failed)
                if self._is_suspicious_untranslated(source_text, translated_text):
                    suspicious_unchanged_chunks += 1
                translated_count += 1
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total)
        else:
            pending_queue: Deque[tuple[int, str]] = deque(pending)
            with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                futures: dict[Future[GuardedSegmentResult], tuple[int, str]] = {}
                self._submit_until_target(executor, futures, pending_queue)
                try:
                    while futures:
                        done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                        for future in done:
                            idx, source_text = futures.pop(future)
                            outcome = future.result()
                            translated_text = self._preserve_line_ending_suffix(source_text, outcome.text)
                            translated_parts[idx] = translated_text
                            guard_fallback_chunks += int(outcome.used_fallback)
                            recovered_chunks += int(outcome.recovered)
                            hard_failed_chunks += int(outcome.hard_failed)
                            if self._is_suspicious_untranslated(source_text, translated_text):
                                suspicious_unchanged_chunks += 1
                            translated_count += 1
                            completed += 1
                            if progress_callback is not None:
                                progress_callback(completed, total)
                        self._submit_until_target(executor, futures, pending_queue)
                except Exception:
                    pending_queue.clear()
                    for future in futures:
                        future.cancel()
                    raise

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
            recovered_chunks=recovered_chunks,
            hard_failed_chunks=hard_failed_chunks,
            suspicious_unchanged_chunks=suspicious_unchanged_chunks,
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

    def _submit_until_target(
        self,
        executor: ThreadPoolExecutor,
        futures: dict[Future[GuardedSegmentResult], tuple[int, str]],
        pending_queue: Deque[tuple[int, str]],
    ) -> None:
        while pending_queue and len(futures) < self.concurrency:
            idx, source_text = pending_queue.popleft()
            futures[executor.submit(self._translate_segment_with_guard, source_text)] = (idx, source_text)

    def _translate_segment_with_guard(self, source_segment: str) -> GuardedSegmentResult:
        combined_metrics = ChunkMetrics()
        source_placeholders = self._placeholder_counter(source_segment)
        if not source_placeholders:
            translated, metrics = self.client.translate_chunk_with_metrics(source_segment)
            combined_metrics.merge(metrics)
            return GuardedSegmentResult(
                text=translated,
                used_fallback=False,
                recovered=False,
                hard_failed=False,
                metrics=combined_metrics,
            )

        # 占位符丢失会直接导致公式/代码/URL 破坏，这里进行额外守护重试。
        for _ in range(3):
            translated, metrics = self.client.translate_chunk_with_metrics(source_segment)
            combined_metrics.merge(metrics)
            if self._placeholder_counter(translated) == source_placeholders and not self._has_placeholder_artifacts(
                translated
            ):
                return GuardedSegmentResult(
                    text=translated,
                    used_fallback=False,
                    recovered=False,
                    hard_failed=False,
                    metrics=combined_metrics,
                )

        recovered = self._recover_segment_by_subchunks(source_segment, source_placeholders, combined_metrics)
        if recovered is not None:
            return GuardedSegmentResult(
                text=recovered,
                used_fallback=False,
                recovered=True,
                hard_failed=False,
                metrics=combined_metrics,
            )

        return GuardedSegmentResult(
            text=source_segment,
            used_fallback=True,
            recovered=False,
            hard_failed=True,
            metrics=combined_metrics,
        )

    @staticmethod
    def _placeholder_counter(text: str) -> Counter[str]:
        return Counter(PLACEHOLDER_PATTERN.findall(text))

    @staticmethod
    def _has_placeholder_artifacts(text: str) -> bool:
        if "@@__MDTZ_" not in text:
            return False
        cleaned = PLACEHOLDER_PATTERN.sub("", text)
        return "@@__MDTZ_" in cleaned

    def _recover_segment_by_subchunks(
        self,
        source_segment: str,
        source_placeholders: Counter[str],
        combined_metrics: ChunkMetrics,
    ) -> str | None:
        for round_index in range(RECOVERY_ROUNDS):
            recovery_max_chars = max(160, RECOVERY_BASE_MAX_CHARS - round_index * 140)
            subchunks = self._split_segment_for_recovery(source_segment, recovery_max_chars)
            translated_chunks: List[str] = []
            round_failed = False

            for chunk in subchunks:
                if self._should_skip_chunk(chunk):
                    translated_chunks.append(chunk)
                    continue

                translated, metrics = self.client.translate_chunk_with_metrics(chunk)
                combined_metrics.merge(metrics)

                chunk_source_placeholders = self._placeholder_counter(chunk)
                if chunk_source_placeholders:
                    translated = self._repair_placeholder_tokens(chunk, translated)
                    if self._placeholder_counter(translated) != chunk_source_placeholders or self._has_placeholder_artifacts(
                        translated
                    ):
                        round_failed = True
                        break
                translated_chunks.append(translated)

            if round_failed:
                continue

            candidate = "".join(translated_chunks)
            candidate = self._repair_placeholder_tokens(source_segment, candidate)
            if self._placeholder_counter(candidate) == source_placeholders and not self._has_placeholder_artifacts(
                candidate
            ):
                return candidate
        return None

    @staticmethod
    def _split_segment_for_recovery(text: str, max_chars: int) -> List[str]:
        lines = text.splitlines(keepends=True)
        if not lines:
            return [text] if text else []

        chunks: List[str] = []
        for line in lines:
            if len(line) <= max_chars:
                chunks.append(line)
                continue

            line_break_match = re.search(r"(\r\n|\n|\r)$", line)
            line_break = line_break_match.group(1) if line_break_match else ""
            line_body = line[: -len(line_break)] if line_break else line

            sentence_units = MarkdownTranslator._split_by_sentence(line_body)
            current = ""
            for unit in sentence_units:
                if not unit:
                    continue
                if len(unit) > max_chars:
                    if current:
                        chunks.append(current)
                        current = ""
                    chunks.extend(_split_by_lines(unit, max_chars))
                    continue
                if len(current) + len(unit) <= max_chars:
                    current += unit
                else:
                    if current:
                        chunks.append(current)
                    current = unit

            if current:
                chunks.append(current + line_break)
            elif chunks and line_break:
                chunks[-1] = chunks[-1] + line_break
            elif line_break:
                chunks.append(line_break)
        return chunks

    @staticmethod
    def _split_by_sentence(text: str) -> List[str]:
        if not text:
            return []
        pieces: List[str] = []
        start = 0
        for match in SENTENCE_SPLIT_PATTERN.finditer(text):
            end = match.end()
            pieces.append(text[start:end])
            start = end
        if start < len(text):
            pieces.append(text[start:])
        return pieces if pieces else [text]

    @staticmethod
    def _repair_placeholder_tokens(source_text: str, translated_text: str) -> str:
        source_tokens = PLACEHOLDER_PATTERN.findall(source_text)
        if not source_tokens:
            return translated_text

        source_counter = Counter(source_tokens)
        repaired = translated_text
        current_tokens = PLACEHOLDER_PATTERN.findall(repaired)
        if not current_tokens:
            return translated_text

        current_counter = Counter(PLACEHOLDER_PATTERN.findall(repaired))
        if not set(current_counter).issubset(set(source_counter)):
            return translated_text
        if any(count > source_counter[token] for token, count in current_counter.items()):
            return translated_text
        if current_counter == source_counter:
            return repaired

        # 仅补齐缺失占位符，避免重排或替换导致语义错位。
        for token in source_tokens:
            if current_counter[token] >= source_counter[token]:
                continue
            insert_at = MarkdownTranslator._find_insert_position(repaired, source_tokens, token)
            if insert_at < 0:
                return translated_text
            prefix = "" if insert_at == 0 or repaired[max(insert_at - 1, 0)].isspace() else " "
            repaired = f"{repaired[:insert_at]}{prefix}{token} {repaired[insert_at:]}"
            current_counter[token] += 1
        return repaired

    @staticmethod
    def _find_insert_position(text: str, ordered_tokens: List[str], token: str) -> int:
        token_index = ordered_tokens.index(token)
        for prev_index in range(token_index - 1, -1, -1):
            prev = ordered_tokens[prev_index]
            location = text.find(prev)
            if location != -1:
                return location + len(prev)
        for next_index in range(token_index + 1, len(ordered_tokens)):
            nxt = ordered_tokens[next_index]
            location = text.find(nxt)
            if location != -1:
                return location
        return -1

    @staticmethod
    def _strip_placeholders(text: str) -> str:
        return PLACEHOLDER_PATTERN.sub("", text)

    @staticmethod
    def _is_suspicious_untranslated(source_segment: str, translated_segment: str) -> bool:
        source = WHITESPACE_PATTERN.sub(" ", MarkdownTranslator._strip_placeholders(source_segment)).strip()
        target = WHITESPACE_PATTERN.sub(" ", MarkdownTranslator._strip_placeholders(translated_segment)).strip()

        if len(source) < SUSPICIOUS_MIN_CHARS:
            return False
        source_words = LATIN_WORD_PATTERN.findall(source)
        if len(source_words) < SUSPICIOUS_MIN_WORDS:
            return False

        target_words = LATIN_WORD_PATTERN.findall(target)
        if len(target_words) < max(12, len(source_words) // 2):
            return False
        if len(CJK_PATTERN.findall(target)) > SUSPICIOUS_MAX_CJK:
            return False

        similarity = SequenceMatcher(None, source.lower(), target.lower()).ratio()
        return similarity >= SUSPICIOUS_SIMILARITY


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

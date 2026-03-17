from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from markdown_it import MarkdownIt

PLACEHOLDER_PATTERN = re.compile(r"@@__MDTZ_[A-Z]+_\d{5}__@@")

REFERENCE_HEADINGS = {
    "references",
    "reference",
    "bibliography",
    "works cited",
    "citations",
    "references and notes",
    "参考文献",
}

REFERENCE_LINE_PATTERNS = [
    re.compile(r"^\s*\[\d+\]\s+.+"),
    re.compile(r"^\s*\[[^\]]+\]:\s+\S+.*"),  # Markdown link reference definitions
    re.compile(r"^\s*(doi|arxiv)\s*:\s*\S+.*", re.IGNORECASE),
]

INLINE_PROTECT_PATTERNS = [
    re.compile(r"`[^`\n]+`"),
    re.compile(r"(?<!\w)https?://[^\s<>)\]]+", re.IGNORECASE),
    re.compile(r"<https?://[^>\s]+>", re.IGNORECASE),
    re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\n\\])+(?<!\\)\$(?!\$)"),
    re.compile(r"\\\([^\n]*?\\\)"),
    re.compile(r"\\\[[^\n]*?\\\]"),
]


@dataclass
class MaskedMarkdown:
    masked_text: str
    replacements: Dict[str, str]


class MarkdownMasker:
    def __init__(self, skip_reference_sections: bool = True, skip_reference_lines: bool = True) -> None:
        self.skip_reference_sections = skip_reference_sections
        self.skip_reference_lines = skip_reference_lines
        self._counter = 0

    def mask(self, markdown_text: str) -> MaskedMarkdown:
        replacements: Dict[str, str] = {}
        working = markdown_text
        working = self._protect_front_matter(working, replacements)
        working = self._protect_block_ranges(working, replacements)
        working = self._protect_math_blocks(working, replacements)
        working = self._protect_inline_patterns(working, replacements)
        return MaskedMarkdown(masked_text=working, replacements=replacements)

    @staticmethod
    def unmask(masked_text: str, replacements: Dict[str, str]) -> str:
        restored = masked_text
        for placeholder in sorted(replacements.keys(), key=len, reverse=True):
            restored = restored.replace(placeholder, replacements[placeholder])
        return restored

    @staticmethod
    def find_placeholders(text: str) -> List[str]:
        return sorted(set(PLACEHOLDER_PATTERN.findall(text)))

    def _protect_front_matter(self, text: str, replacements: Dict[str, str]) -> str:
        match = re.match(r"\A---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)", text)
        if not match:
            return text
        placeholder = self._stash(match.group(0), replacements, tag="BLOCK")
        return placeholder + text[match.end() :]

    def _protect_block_ranges(self, text: str, replacements: Dict[str, str]) -> str:
        lines = text.splitlines(keepends=True)
        if not lines:
            return text

        ranges: List[Tuple[int, int]] = []
        md = MarkdownIt("commonmark", {"html": True})
        tokens = md.parse(text)

        for idx, token in enumerate(tokens):
            if token.map is None:
                continue

            if token.type in {"fence", "code_block", "html_block"}:
                ranges.append((token.map[0], token.map[1]))
                continue

            if self.skip_reference_sections and token.type == "heading_open":
                if idx + 1 < len(tokens) and tokens[idx + 1].type == "inline":
                    heading_text = self._normalize_heading(tokens[idx + 1].content)
                    if heading_text in REFERENCE_HEADINGS:
                        start = token.map[0]
                        end = len(lines)
                        for tail in tokens[idx + 1 :]:
                            if tail.type == "heading_open" and tail.map and tail.map[0] > start:
                                end = tail.map[0]
                                break
                        ranges.append((start, end))

        ranges.extend(self._collect_math_block_ranges(lines))

        if self.skip_reference_lines:
            for line_index, line in enumerate(lines):
                if self._is_reference_line(line):
                    ranges.append((line_index, line_index + 1))

        merged_ranges = self._merge_ranges(ranges)
        return self._replace_line_ranges(lines, merged_ranges, replacements)

    def _protect_math_blocks(self, text: str, replacements: Dict[str, str]) -> str:
        protected = text
        block_patterns = [
            re.compile(r"(?ms)^\$\$\s*\n.*?\n^\$\$\s*$"),
            re.compile(r"(?ms)^\\\[\s*\n.*?\n\\\]\s*$"),
            re.compile(
                r"(?ms)\\begin\{(equation\*?|align\*?|aligned|gather\*?|multline\*?|eqnarray\*?|split)\}.*?\\end\{\1\}"
            ),
        ]
        for pattern in block_patterns:
            protected = pattern.sub(lambda m: self._stash(m.group(0), replacements, tag="BLOCK"), protected)
        return protected

    def _protect_inline_patterns(self, text: str, replacements: Dict[str, str]) -> str:
        protected = text
        for pattern in INLINE_PROTECT_PATTERNS:
            protected = pattern.sub(lambda m: self._stash(m.group(0), replacements, tag="INLINE"), protected)
        return protected

    def _collect_math_block_ranges(self, lines: List[str]) -> List[Tuple[int, int]]:
        ranges: List[Tuple[int, int]] = []
        start_index: int | None = None

        for idx, line in enumerate(lines):
            if line.strip().startswith("$$"):
                if start_index is None:
                    start_index = idx
                else:
                    ranges.append((start_index, idx + 1))
                    start_index = None

        if start_index is not None:
            ranges.append((start_index, len(lines)))
        return ranges

    def _replace_line_ranges(
        self,
        lines: List[str],
        ranges: Iterable[Tuple[int, int]],
        replacements: Dict[str, str],
    ) -> str:
        out: List[str] = []
        cursor = 0

        for start, end in ranges:
            out.extend(lines[cursor:start])
            block = "".join(lines[start:end])
            placeholder = self._new_placeholder(replacements, tag="BLOCK")
            replacements[placeholder] = block

            out.append(placeholder)
            cursor = end

        out.extend(lines[cursor:])
        return "".join(out)

    @staticmethod
    def _merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        normalized = sorted((start, end) for start, end in ranges if start < end)
        if not normalized:
            return []

        merged: List[Tuple[int, int]] = [normalized[0]]
        for start, end in normalized[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _normalize_heading(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    @staticmethod
    def _is_reference_line(line: str) -> bool:
        return any(pattern.match(line.strip()) for pattern in REFERENCE_LINE_PATTERNS)

    def _stash(self, source_text: str, replacements: Dict[str, str], tag: str) -> str:
        placeholder = self._new_placeholder(replacements, tag=tag)
        replacements[placeholder] = source_text
        return placeholder

    def _new_placeholder(self, replacements: Dict[str, str], tag: str) -> str:
        while True:
            self._counter += 1
            placeholder = f"@@__MDTZ_{tag}_{self._counter:05d}__@@"
            if placeholder not in replacements:
                return placeholder

from __future__ import annotations

import re
from dataclasses import dataclass

COPYRIGHT_START_PATTERN = re.compile(r"(?i)\bcopyright\b|©")
LEGAL_KEYWORDS = [
    re.compile(r"(?i)\bright[s]?\s+reserved\b"),
    re.compile(r"(?i)\bexclusive\b"),
    re.compile(r"(?i)\blicensee\b"),
    re.compile(r"(?i)\bterms\s+of\s+service\b"),
    re.compile(r"(?i)\bpermission[s]?\b"),
    re.compile(r"(?i)\breprints?\b"),
    re.compile(r"(?i)\bgovernment\s+works?\b"),
    re.compile(r"(?i)\bassociation\s+for\s+the\s+advancement\s+of\s+science\b"),
]


@dataclass
class CleanupResult:
    text: str
    removed_blocks: int


@dataclass
class ReflowResult:
    text: str
    merged_breaks: int


TERMINAL_PUNCTUATION = re.compile(r"[.!?。！？:：;；]$")
LIST_ITEM_PATTERN = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+")
QUOTE_PATTERN = re.compile(r"^\s{0,3}>")
TABLE_PATTERN = re.compile(r"^\s*\|")
IMAGE_PATTERN = re.compile(r"^\s*!\[.*\]\(.*\)\s*$")
REFERENCE_ITEM_PATTERN = re.compile(r"^\s*\[\d+\]\s+")
LINK_DEF_PATTERN = re.compile(r"^\s*\[[^\]]+\]:\s+")
PLACEHOLDER_PATTERN = re.compile(r"^@@__MDTZ_[A-Z]+_\d{5}__@@$")
CONNECTOR_WORDS = {
    "and",
    "or",
    "of",
    "to",
    "for",
    "with",
    "by",
    "in",
    "on",
    "at",
    "from",
    "between",
    "through",
    "that",
    "which",
}


def remove_legal_boilerplate(markdown_text: str) -> CleanupResult:
    lines = markdown_text.splitlines(keepends=True)
    if not lines:
        return CleanupResult(text=markdown_text, removed_blocks=0)

    out: list[str] = []
    removed_blocks = 0
    index = 0
    total = len(lines)

    while index < total:
        if not COPYRIGHT_START_PATTERN.search(lines[index]):
            out.append(lines[index])
            index += 1
            continue

        end = _find_candidate_end(lines, index)
        candidate = "".join(lines[index:end])
        if not _looks_like_legal_boilerplate(candidate):
            out.append(lines[index])
            index += 1
            continue

        removed_blocks += 1
        while out and not out[-1].strip():
            out.pop()

        index = end
        while index < total and not lines[index].strip():
            index += 1

        if out and (not out[-1].endswith("\n")):
            out[-1] = out[-1] + "\n"

    return CleanupResult(text="".join(out), removed_blocks=removed_blocks)


def _find_candidate_end(lines: list[str], start: int) -> int:
    max_window = min(len(lines), start + 80)
    index = start + 1
    while index < max_window:
        stripped = lines[index].strip()
        if stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("![]("):
            break

        if stripped and len(stripped) > 100 and not _contains_legal_keyword(stripped):
            break

        index += 1
    return index


def _looks_like_legal_boilerplate(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False
    if "copyright" not in normalized and "©" not in normalized:
        return False

    hits = sum(1 for pattern in LEGAL_KEYWORDS if pattern.search(normalized))
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    mostly_short = bool(non_empty_lines) and (
        sum(1 for line in non_empty_lines if len(line.strip()) <= 60) / len(non_empty_lines) >= 0.7
    )
    return hits >= 2 and mostly_short


def _contains_legal_keyword(text: str) -> bool:
    return any(pattern.search(text) for pattern in LEGAL_KEYWORDS)


def normalize_ocr_line_breaks(markdown_text: str) -> ReflowResult:
    lines = markdown_text.splitlines(keepends=True)
    if not lines:
        return ReflowResult(text=markdown_text, merged_breaks=0)

    out: list[str] = []
    merged = 0
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        if line.strip():
            out.append(line)
            index += 1
            continue

        prev = _last_non_empty_from_out(out)
        next_idx = _find_next_non_empty(lines, index + 1)
        if prev is None or next_idx is None:
            out.append(line)
            index += 1
            continue

        nxt = lines[next_idx]
        if _should_merge_across_blank(prev, nxt):
            out[-1] = re.sub(r"(?:\r\n|\n|\r)+$", "", out[-1]) + " "
            merged += 1
            index += 1
            continue

        out.append(line)
        index += 1

    return ReflowResult(text="".join(out), merged_breaks=merged)


def _last_non_empty_from_out(lines: list[str]) -> str | None:
    for item in reversed(lines):
        if item.strip():
            return item
    return None


def _find_next_non_empty(lines: list[str], start: int) -> int | None:
    for idx in range(start, len(lines)):
        if lines[idx].strip():
            return idx
    return None


def _should_merge_across_blank(prev_line: str, next_line: str) -> bool:
    prev = prev_line.strip()
    nxt = next_line.strip()
    if not prev or not nxt:
        return False

    if _is_structured_markdown_line(prev) or _is_structured_markdown_line(nxt):
        return False
    if TERMINAL_PUNCTUATION.search(prev):
        return False

    last_token = prev.split()[-1].lower().strip("\"'()[]{}<>.,;:!?")
    if last_token in CONNECTOR_WORDS:
        return True

    first_char = nxt[0]
    if re.match(r"[a-z0-9(\[\"'$]", first_char):
        return True
    if re.match(r"[\u4e00-\u9fff]", first_char):
        return True
    return False


def _is_structured_markdown_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if PLACEHOLDER_PATTERN.match(stripped):
        return True
    if HEADING_PATTERN.match(stripped):
        return True
    if LIST_ITEM_PATTERN.match(stripped):
        return True
    if QUOTE_PATTERN.match(stripped):
        return True
    if TABLE_PATTERN.match(stripped):
        return True
    if IMAGE_PATTERN.match(stripped):
        return True
    if stripped.startswith("```") or stripped.startswith("~~~") or stripped.startswith("$$"):
        return True
    if REFERENCE_ITEM_PATTERN.match(stripped):
        return True
    if LINK_DEF_PATTERN.match(stripped):
        return True
    return False

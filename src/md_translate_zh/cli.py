from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

from .cleaner import remove_legal_boilerplate
from .client import TranslationClient
from .config import AppConfig
from .translator import MarkdownTranslator

FORMULA_ENV_PATTERN = r"(equation\*?|align\*?|aligned|gather\*?|multline\*?|eqnarray\*?|split)"

INTEGRITY_CHECKS: List[Tuple[str, re.Pattern[str]]] = [
    ("块公式 $$", re.compile(r"(?m)^\s*\$\$\s*$")),
    ("行内公式 $...$", re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\r\n\\])+(?<!\\)\$(?!\$)")),
    ("行内公式 \\(...\\)", re.compile(r"\\\([^\n]*?\\\)")),
    ("块公式 \\[...\\]", re.compile(r"(?ms)^\\\[\s*\n.*?\n\\\]\s*$")),
    ("LaTeX 环境 begin", re.compile(rf"\\begin\{{{FORMULA_ENV_PATTERN}\}}")),
    ("LaTeX 环境 end", re.compile(rf"\\end\{{{FORMULA_ENV_PATTERN}\}}")),
    ("代码围栏 ```", re.compile(r"(?m)^\s*```")),
    ("Markdown 链接", re.compile(r"(?<!!)\[[^\]\n]+\]\([^)]+\)")),
    ("Markdown 图片", re.compile(r"!\[[^\]\n]*\]\([^)]+\)")),
]

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="md-zh-translator",
        description="将英文 Markdown 翻译成中文，同时尽量保留原始格式并跳过参考文献等内容。",
    )
    parser.add_argument("-i", "--input", required=True, help="输入 Markdown 文件路径")
    parser.add_argument("-o", "--output", help="输出 Markdown 文件路径，默认在原文件名后追加 .zh")

    parser.add_argument("--api-key", help="API Key（真翻译时必填）")
    parser.add_argument("--base-url", help="API Base URL（默认 https://api.openai.com/v1）")
    parser.add_argument("--model", help="模型名（默认 gpt-4o-mini）")

    parser.add_argument("--max-chars", type=int, help="每个翻译分片最大字符数")
    parser.add_argument("--temperature", type=float, help="采样温度")
    parser.add_argument("--max-retries", type=int, help="请求失败重试次数")
    parser.add_argument("--timeout", type=float, help="请求超时秒数")
    parser.add_argument("--max-rpm", type=int, help="每分钟最大请求数（RPM）")
    parser.add_argument("--max-tpm", type=int, help="每分钟最大令牌数（TPM，按本地估算+响应校正）")

    parser.add_argument(
        "--translate-references",
        action="store_true",
        help="默认会跳过参考文献区域，打开此开关后会尝试翻译参考文献。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不调用 API，仅执行保护/还原流程并输出结果（用于验证格式保真）。",
    )
    parser.add_argument(
        "--keep-boilerplate",
        action="store_true",
        help="默认自动移除版权/许可声明等噪声块；打开此开关可保留原文。",
    )
    parser.add_argument(
        "--keep-ocr-linebreaks",
        action="store_true",
        help="默认会自动修复 OCR 造成的段内断行；打开此开关可保留原始断行。",
    )
    parser.add_argument(
        "--strict-integrity",
        action="store_true",
        help="对公式标记完整性执行严格校验，发现异常时返回非 0 状态码。",
    )
    parser.add_argument("--verbose", action="store_true", help="显示分片处理进度")
    return parser


def default_output_path(input_path: Path) -> Path:
    if input_path.suffix:
        return input_path.with_name(f"{input_path.stem}.zh{input_path.suffix}")
    return input_path.with_name(f"{input_path.name}.zh.md")


def _collect_integrity_issues(source: str, target: str) -> List[str]:
    issues: List[str] = []
    for label, pattern in INTEGRITY_CHECKS:
        source_count = len(pattern.findall(source))
        target_count = len(pattern.findall(target))
        if source_count != target_count:
            issues.append(f"{label}: {source_count} -> {target_count}")
    return issues


def _is_exact_match(source: str, target: str) -> bool:
    return source == target


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"输入路径不是文件: {input_path}", file=sys.stderr)
        return 2

    output_path = Path(args.output).expanduser().resolve() if args.output else default_output_path(input_path)

    try:
        config = AppConfig.from_args(args, require_api=not args.dry_run)
    except ValueError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    try:
        with input_path.open("r", encoding="utf-8", newline="") as file:
            markdown_text = file.read()
    except UnicodeDecodeError:
        print(f"读取失败：{input_path} 不是 UTF-8 编码。", file=sys.stderr)
        return 2

    apply_cleanup = not args.keep_boilerplate and not args.dry_run
    if apply_cleanup:
        cleaned = remove_legal_boilerplate(markdown_text)
        markdown_text = cleaned.text
        if args.verbose and cleaned.removed_blocks > 0:
            print(f"已自动移除版权/许可噪声块: {cleaned.removed_blocks}", file=sys.stderr)
    elif args.verbose and args.dry_run:
        print("dry-run 模式下已跳过版权/许可清理。", file=sys.stderr)

    client = TranslationClient(config)
    translator = MarkdownTranslator(
        client=client,
        max_chars=config.max_chars,
        skip_reference_sections=not args.translate_references,
        skip_reference_lines=not args.translate_references,
        normalize_ocr_breaks=(not args.keep_ocr_linebreaks) and (not args.dry_run),
    )

    def on_progress(index: int, total: int) -> None:
        if args.verbose:
            print(f"[{index}/{total}] 处理分片...", file=sys.stderr)

    try:
        result = translator.translate(
            markdown_text=markdown_text,
            dry_run=args.dry_run,
            progress_callback=on_progress,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"翻译失败: {exc}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        file.write(result.text)

    print(f"完成: {input_path} -> {output_path}")
    print(
        f"分片: {result.total_chunks}，实际调用翻译: {result.translated_chunks}，保护片段: {result.protected_items}"
    )
    if args.verbose and result.merged_breaks > 0:
        print(f"已自动修复段内断行: {result.merged_breaks}", file=sys.stderr)
    if args.verbose and result.guard_fallback_chunks > 0:
        print(f"占位符守护回退分片: {result.guard_fallback_chunks}", file=sys.stderr)

    integrity_issues = _collect_integrity_issues(markdown_text, result.text)
    if integrity_issues:
        print("警告: 检测到结构完整性差异：", file=sys.stderr)
        for issue in integrity_issues:
            print(f"  - {issue}", file=sys.stderr)

    if result.unresolved_placeholders:
        print(
            "警告: 输出中仍有未还原占位符，请检查模型是否改写了占位符字符串。",
            file=sys.stderr,
        )
        if args.verbose:
            preview = ", ".join(result.unresolved_placeholders[:5])
            print(f"未还原占位符示例: {preview}", file=sys.stderr)

    if args.dry_run and not _is_exact_match(markdown_text, result.text):
        print("警告: dry-run 输出与输入不一致。", file=sys.stderr)
        if args.verbose:
            print("建议检查是否有占位符保护/还原逻辑被破坏。", file=sys.stderr)

    if args.verbose and not integrity_issues and not result.unresolved_placeholders:
        print("完整性检查通过（公式/链接/图片/代码围栏/占位符）。", file=sys.stderr)

    if args.strict_integrity:
        if integrity_issues or result.unresolved_placeholders:
            return 1
        if args.dry_run and not _is_exact_match(markdown_text, result.text):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
import zipfile


EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "artifacts",
    ".mdzt_update_backups",
}

EXCLUDE_FILE_PATTERNS = (
    "*.pyc",
    "*.pyo",
    "*.zh.md",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="构建 md-zh-translator 发布资产：md-zh-translator.zip + update.json"
    )
    parser.add_argument("--version", required=True, help="发布版本号，例如 0.1.1")
    parser.add_argument(
        "--repo",
        default="siri666666/md-zh-translator",
        help="GitHub 仓库，格式 owner/repo",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/release",
        help="发布资产输出目录",
    )
    parser.add_argument(
        "--zip-name",
        default="md-zh-translator.zip",
        help="发布包文件名",
    )
    parser.add_argument(
        "--notes-url",
        default=None,
        help="发布说明链接，默认自动拼接为 releases/tag/v<version>",
    )
    parser.add_argument(
        "--published-at",
        default=None,
        help="发布时间（ISO8601），默认当前 UTC 时间",
    )
    parser.add_argument(
        "--changelog",
        action="append",
        default=[],
        help="更新内容，可重复传入多次",
    )
    parser.add_argument(
        "--changelog-file",
        default=None,
        help="更新内容文本文件（每行一条）",
    )
    return parser.parse_args()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_excluded(rel_path: Path, output_dir: Path) -> bool:
    parts = set(rel_path.parts)
    if parts & EXCLUDE_DIR_NAMES:
        return True

    rel_posix = rel_path.as_posix()
    output_posix = output_dir.as_posix().rstrip("/")
    if rel_posix == output_posix or rel_posix.startswith(output_posix + "/"):
        return True

    for pattern in EXCLUDE_FILE_PATTERNS:
        if fnmatch(rel_path.name, pattern) or fnmatch(rel_posix, pattern):
            return True
    return False


def iter_project_files(root: Path, output_dir: Path):
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_dir = current_path.relative_to(root)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in EXCLUDE_DIR_NAMES
            and not is_excluded(
                (rel_dir / name) if rel_dir != Path(".") else Path(name),
                output_dir,
            )
        ]
        for filename in filenames:
            rel_file = (rel_dir / filename) if rel_dir != Path(".") else Path(filename)
            if is_excluded(rel_file, output_dir):
                continue
            yield current_path / filename, rel_file


def build_zip(root: Path, output_zip: Path, output_dir: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_file, rel_file in iter_project_files(root, output_dir):
            zf.write(abs_file, arcname=rel_file.as_posix())


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_changelog(args: argparse.Namespace) -> list[str]:
    items = [item.strip() for item in args.changelog if item.strip()]
    if args.changelog_file:
        file_path = Path(args.changelog_file).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"changelog 文件不存在: {file_path}")
        text = file_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            line = line.lstrip("-").strip()
            if line:
                items.append(line)
    return items


def main() -> int:
    args = parse_args()
    root = project_root()
    output_dir = (root / args.output_dir).resolve()
    output_zip = output_dir / args.zip_name

    changelog = load_changelog(args)
    if not changelog:
        raise ValueError("至少提供一条 changelog（--changelog 或 --changelog-file）")

    build_zip(root=root, output_zip=output_zip, output_dir=Path(args.output_dir))
    sha256 = sha256_of_file(output_zip)

    notes_url = (
        args.notes_url
        or f"https://github.com/{args.repo}/releases/tag/v{args.version.lstrip('vV')}"
    )
    published_at = args.published_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    zip_url = f"https://github.com/{args.repo}/releases/latest/download/{args.zip_name}"

    metadata = {
        "version": args.version,
        "zip_url": zip_url,
        "sha256": sha256,
        "changelog": changelog,
        "notes_url": notes_url,
        "published_at": published_at,
    }
    update_json = output_dir / "update.json"
    update_json.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("发布资产已生成：")
    print(f"- ZIP: {output_zip}")
    print(f"- update.json: {update_json}")
    print(f"- sha256: {sha256}")
    print("- 发布时请上传这两个文件到 GitHub Release。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

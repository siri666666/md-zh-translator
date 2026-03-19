from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable


UPDATE_METADATA_URL = (
    "https://github.com/siri666666/md-zh-translator/releases/latest/download/update.json"
)
NETWORK_TIMEOUT_SECONDS = 20
RETRY_DELAYS_SECONDS = (0.0, 1.5, 3.0)
MAX_BACKUP_DIRS = 3

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
    ".env",
    ".env.*",
)


@dataclass(frozen=True)
class UpdateMetadata:
    version: str
    zip_url: str
    sha256: str
    changelog: list[str]
    notes_url: str | None
    published_at: str | None


@dataclass(frozen=True)
class UpdatePlan:
    unchanged: int
    skipped: int
    new_files: list[Path]
    updated_files: list[Path]


class UpdateError(RuntimeError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parent


def read_local_version(root: Path) -> str:
    init_file = root / "src" / "md_translate_zh" / "__init__.py"
    if not init_file.exists():
        raise UpdateError(f"未找到版本文件: {init_file}")
    content = init_file.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise UpdateError(f"无法从 {init_file} 解析 __version__")
    return match.group(1).strip()


def parse_semver(version: str) -> tuple[int, int, int]:
    text = version.strip().lstrip("vV")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not match:
        raise UpdateError(f"无效版本号: {version}")
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return major, minor, patch


def normalize_changelog(raw: object) -> list[str]:
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw if str(item).strip()]
        return items
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]
    return []


def fetch_json_with_retry(url: str) -> dict:
    headers = {"User-Agent": "md-zh-translator-updater/1.0"}
    last_exc: Exception | None = None
    for delay in RETRY_DELAYS_SECONDS:
        if delay > 0:
            time.sleep(delay)
        try:
            req = urllib.request.Request(url=url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_SECONDS) as resp:
                payload = resp.read().decode("utf-8")
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise UpdateError("update.json 顶层必须是对象")
                return data
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc is None:
        raise UpdateError("未知网络错误")
    if isinstance(last_exc, urllib.error.HTTPError):
        raise UpdateError(
            f"拉取更新元数据失败: HTTP {last_exc.code} ({url})"
        ) from last_exc
    raise UpdateError(f"拉取更新元数据失败: {last_exc}") from last_exc


def parse_metadata(payload: dict) -> UpdateMetadata:
    required = ("version", "zip_url", "sha256", "changelog")
    missing = [key for key in required if key not in payload]
    if missing:
        raise UpdateError(f"update.json 缺少字段: {', '.join(missing)}")

    version = str(payload["version"]).strip()
    zip_url = str(payload["zip_url"]).strip()
    sha256 = str(payload["sha256"]).strip().lower()
    changelog = normalize_changelog(payload["changelog"])
    notes_url = payload.get("notes_url")
    published_at = payload.get("published_at")

    if not version:
        raise UpdateError("update.json 字段 version 为空")
    if not zip_url.startswith("http://") and not zip_url.startswith("https://"):
        raise UpdateError("update.json 字段 zip_url 必须是 http(s) 地址")
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise UpdateError("update.json 字段 sha256 必须是 64 位十六进制")
    if not changelog:
        raise UpdateError("update.json 字段 changelog 不能为空")
    if notes_url is not None:
        notes_url = str(notes_url).strip() or None
    if published_at is not None:
        published_at = str(published_at).strip() or None

    return UpdateMetadata(
        version=version,
        zip_url=zip_url,
        sha256=sha256,
        changelog=changelog,
        notes_url=notes_url,
        published_at=published_at,
    )


def compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_with_retry(url: str, destination: Path) -> None:
    headers = {"User-Agent": "md-zh-translator-updater/1.0"}
    last_exc: Exception | None = None
    for delay in RETRY_DELAYS_SECONDS:
        if delay > 0:
            time.sleep(delay)
        try:
            req = urllib.request.Request(url=url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_SECONDS) as resp:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as out:
                    shutil.copyfileobj(resp, out)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc is None:
        raise UpdateError("未知下载错误")
    if isinstance(last_exc, urllib.error.HTTPError):
        raise UpdateError(f"下载更新包失败: HTTP {last_exc.code} ({url})") from last_exc
    raise UpdateError(f"下载更新包失败: {last_exc}") from last_exc


def safe_extract_zip(zip_path: Path, extract_to: Path) -> None:
    target_root = extract_to.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = (extract_to / member.filename).resolve()
            try:
                member_path.relative_to(target_root)
            except ValueError as exc:
                raise UpdateError(f"Zip 包含非法路径: {member.filename}")
            zf.extract(member, extract_to)


def looks_like_project_root(path: Path) -> bool:
    return (path / "src" / "md_translate_zh" / "__init__.py").exists()


def locate_source_root(extracted_root: Path) -> Path:
    if looks_like_project_root(extracted_root):
        return extracted_root

    candidates: list[Path] = []
    for entry in extracted_root.iterdir():
        if entry.is_dir() and looks_like_project_root(entry):
            candidates.append(entry)

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise UpdateError("压缩包中包含多个候选项目目录，无法确定更新源")
    raise UpdateError("压缩包中未找到项目根目录（缺少 src/md_translate_zh）")


def is_excluded(rel_path: Path) -> bool:
    parts = set(rel_path.parts)
    if parts & EXCLUDE_DIR_NAMES:
        return True

    rel_posix = rel_path.as_posix()
    for pattern in EXCLUDE_FILE_PATTERNS:
        if fnmatch(rel_path.name, pattern) or fnmatch(rel_posix, pattern):
            return True
    return False


def iter_source_files(source_root: Path) -> Iterable[tuple[Path, Path]]:
    for current_root, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDE_DIR_NAMES]
        current_path = Path(current_root)
        rel_dir = current_path.relative_to(source_root)
        for filename in filenames:
            src_file = current_path / filename
            rel_file = (rel_dir / filename) if rel_dir != Path(".") else Path(filename)
            yield src_file, rel_file


def compare_file_bytes(file1: Path, file2: Path) -> bool:
    if file1.stat().st_size != file2.stat().st_size:
        return False
    with file1.open("rb") as f1, file2.open("rb") as f2:
        while True:
            b1 = f1.read(1024 * 1024)
            b2 = f2.read(1024 * 1024)
            if b1 != b2:
                return False
            if not b1:
                return True


def build_update_plan(source_root: Path, root: Path) -> UpdatePlan:
    unchanged = 0
    skipped = 0
    new_files: list[Path] = []
    updated_files: list[Path] = []

    for src_file, rel_file in iter_source_files(source_root):
        if is_excluded(rel_file):
            skipped += 1
            continue
        target_file = root / rel_file
        if target_file.exists():
            if target_file.is_dir():
                raise UpdateError(f"目标路径是目录，无法覆盖文件: {target_file}")
            if compare_file_bytes(src_file, target_file):
                unchanged += 1
            else:
                updated_files.append(rel_file)
        else:
            new_files.append(rel_file)

    return UpdatePlan(
        unchanged=unchanged,
        skipped=skipped,
        new_files=new_files,
        updated_files=updated_files,
    )


def ensure_backup_dir(root: Path, local_version: str, remote_version: str) -> Path:
    backup_root = root / ".mdzt_update_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"{timestamp}_{local_version}_to_{remote_version}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    return backup_dir


def prune_old_backups(root: Path) -> None:
    backup_root = root / ".mdzt_update_backups"
    if not backup_root.exists():
        return
    dirs = [entry for entry in backup_root.iterdir() if entry.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dirs[MAX_BACKUP_DIRS:]:
        shutil.rmtree(old, ignore_errors=True)


def apply_update(
    source_root: Path,
    root: Path,
    plan: UpdatePlan,
    local_version: str,
    remote_version: str,
) -> tuple[Path, int, int]:
    backup_dir = ensure_backup_dir(root, local_version, remote_version)
    copied_new: list[Path] = []
    copied_updated: list[Path] = []

    try:
        for rel_file in plan.updated_files:
            src = source_root / rel_file
            dst = root / rel_file
            backup_file = backup_dir / rel_file
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup_file)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_updated.append(rel_file)

        for rel_file in plan.new_files:
            src = source_root / rel_file
            dst = root / rel_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_new.append(rel_file)

    except Exception as exc:  # noqa: BLE001
        rollback_update(root, backup_dir, copied_updated, copied_new)
        raise UpdateError(f"应用更新失败，已回滚: {exc}") from exc

    return backup_dir, len(copied_updated), len(copied_new)


def rollback_update(
    root: Path,
    backup_dir: Path,
    copied_updated: list[Path],
    copied_new: list[Path],
) -> None:
    for rel_file in copied_updated:
        backup_file = backup_dir / rel_file
        dst = root / rel_file
        if backup_file.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, dst)
    for rel_file in copied_new:
        dst = root / rel_file
        if dst.exists():
            dst.unlink()


def print_update_info(local_version: str, metadata: UpdateMetadata) -> None:
    print("\n发现新版本：")
    print(f"- 当前版本: {local_version}")
    print(f"- 最新版本: {metadata.version}")
    if metadata.published_at:
        print(f"- 发布时间: {metadata.published_at}")
    print("- 更新内容:")
    for item in metadata.changelog:
        print(f"  - {item}")
    if metadata.notes_url:
        print(f"- 详情链接: {metadata.notes_url}")


def confirm_update() -> bool:
    try:
        answer = input("\n是否立即更新? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n未获取到输入，已取消更新。")
        return False
    return answer in {"y", "yes"}


def run_update(root: Path, metadata: UpdateMetadata, local_version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="mdzt-update-") as tmp_dir:
        temp_root = Path(tmp_dir)
        zip_path = temp_root / "release.zip"
        extract_dir = temp_root / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        print("\n开始下载更新包...")
        download_with_retry(metadata.zip_url, zip_path)

        actual_sha256 = compute_sha256(zip_path)
        if actual_sha256.lower() != metadata.sha256.lower():
            raise UpdateError(
                "校验失败: SHA256 不匹配\n"
                f"- 期望: {metadata.sha256}\n"
                f"- 实际: {actual_sha256}"
            )
        print("更新包校验通过。")

        safe_extract_zip(zip_path, extract_dir)
        source_root = locate_source_root(extract_dir)

        plan = build_update_plan(source_root, root)
        total_to_apply = len(plan.updated_files) + len(plan.new_files)
        print("\n更新预览:")
        print(f"- 待覆盖文件: {len(plan.updated_files)}")
        print(f"- 待新增文件: {len(plan.new_files)}")
        print(f"- 已相同跳过: {plan.unchanged}")
        print(f"- 规则排除: {plan.skipped}")

        if total_to_apply == 0:
            print("本地文件与更新包一致，无需更新。")
            return

        backup_dir, updated_count, new_count = apply_update(
            source_root=source_root,
            root=root,
            plan=plan,
            local_version=local_version,
            remote_version=metadata.version,
        )
        prune_old_backups(root)

        print("\n更新完成。")
        print(f"- 覆盖文件: {updated_count}")
        print(f"- 新增文件: {new_count}")
        print(f"- 备份目录: {backup_dir}")
        print("请重新运行你的翻译命令。")


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> int:
    root = project_root()
    print("md-zh-translator 更新检查")
    print(f"- 项目目录: {root}")
    print(f"- 检查时间(UTC): {now_iso_utc()}")

    try:
        local_version = read_local_version(root)
        payload = fetch_json_with_retry(UPDATE_METADATA_URL)
        metadata = parse_metadata(payload)
    except UpdateError as exc:
        print(f"检查更新失败: {exc}", file=sys.stderr)
        return 1

    print(f"- 当前版本: {local_version}")
    print(f"- 远端版本: {metadata.version}")

    try:
        local_key = parse_semver(local_version)
        remote_key = parse_semver(metadata.version)
    except UpdateError as exc:
        print(f"版本比较失败: {exc}", file=sys.stderr)
        return 1
    if remote_key <= local_key:
        print("已是最新版本，无需更新。")
        return 0

    print_update_info(local_version, metadata)
    if not confirm_update():
        print("已取消更新。")
        return 0

    try:
        run_update(root, metadata, local_version)
    except UpdateError as exc:
        print(f"更新失败: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"更新失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

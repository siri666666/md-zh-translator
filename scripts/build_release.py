from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def detect_platform() -> str:
    machine = platform.machine().lower()
    arch = "x64"
    if machine in {"aarch64", "arm64"}:
        arch = "arm64"

    if sys.platform.startswith("win"):
        return f"windows-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    if sys.platform.startswith("darwin"):
        return f"macos-{arch}"
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def bin_name_for(target_platform: str) -> str:
    if target_platform.startswith("windows-"):
        return "md-zh-translator.exe"
    return "md-zh-translator"


def run_pyinstaller(root: Path, target_platform: str) -> Path:
    entry = root / "scripts" / "pyinstaller_entry.py"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "md-zh-translator",
        "--paths",
        str(root / "src"),
        str(entry),
    ]
    subprocess.run(cmd, check=True, cwd=root)
    built = root / "dist" / bin_name_for(target_platform)
    if not built.exists():
        raise RuntimeError(f"Build failed, binary not found: {built}")
    return built


def package_release(root: Path, binary: Path, version: str, target_platform: str) -> Path:
    artifacts = root / "artifacts"
    staging = artifacts / f"md-zh-translator-{target_platform}"
    zip_path = artifacts / f"md-zh-translator-{target_platform}-{version}.zip"

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    shutil.copy2(binary, staging / binary.name)
    shutil.copy2(root / ".env.example", staging / ".env.example")
    shutil.copy2(root / "README.md", staging / "README.md")
    shutil.copy2(root / "scripts" / "RELEASE_USAGE_zh.md", staging / "USAGE_zh.md")

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(staging.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(staging))
    return zip_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build portable release zip for current platform.")
    parser.add_argument("--version", default="dev", help="Release version suffix, e.g. v0.1.1")
    parser.add_argument(
        "--platform",
        dest="target_platform",
        help="Target platform label for artifact naming, e.g. windows-x64/linux-x64",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    target_platform = args.target_platform or detect_platform()

    print(f"[1/3] Building binary for {target_platform} ...")
    binary = run_pyinstaller(root, target_platform)

    print("[2/3] Packaging release zip ...")
    zip_path = package_release(root, binary, args.version, target_platform)

    print("[3/3] Done")
    print(f"Binary: {binary}")
    print(f"Zip: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

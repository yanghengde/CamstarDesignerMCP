"""Constrain caller-supplied metadata paths to the configured directory."""

from pathlib import Path
from uuid import uuid4

import config


def root_dir() -> Path:
    return Path(config.DESIGNER_ROOT).expanduser().resolve()


def source_path(value: str, suffix: str) -> Path:
    root = root_dir()
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != suffix:
        raise ValueError(f"文件必须位于 DESIGNER_ROOT 内，且扩展名为 {suffix}")
    if not path.is_file():
        raise ValueError(f"找不到元数据文件：{path.name}")
    return path


def artifact_dir() -> Path:
    root = root_dir()
    base = root / "artifacts"
    if not base.resolve().is_relative_to(root):
        raise ValueError("artifacts 目录不能指向 DESIGNER_ROOT 之外")
    folder = base / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    return folder

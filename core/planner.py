# -*- coding: utf-8 -*-
"""命名规划与去重策略。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Set, Tuple

from .utils import sanitize_and_upper

def collect_existing_basenames(folder: Path) -> Set[str]:
    """收集目标目录下（不含扩展名）的基名集合，用于去重。"""
    exists = set()
    for p in folder.glob("*"):
        if p.is_file():
            exists.add(p.stem.upper())
    return exists

def plan_name(base: str, ext: str, existing: Set[str], duplicates: bool) -> str:
    """
    返回最终文件名（含扩展名），existing 里放的是已占用的“基名”（不含扩展名，统一大写）。
    - duplicates=True  时：base-1, base-2, base-3 ...
    - duplicates=False 时：直接 base.ext
    """
    base = sanitize_and_upper(base)
    ext = ext.lower()
    if not duplicates:
        return f"{base}{ext}"

    # 改：从 -1 开始
    idx = 1
    while True:
        stem = f"{base}-{idx}"
        if stem not in existing:
            existing.add(stem)
            return f"{stem}{ext}"
        idx += 1

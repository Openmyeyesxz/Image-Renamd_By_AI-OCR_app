# -*- coding: utf-8 -*-
"""通用工具函数与常量。"""
from __future__ import annotations

import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9\-_]+")

def sanitize_and_upper(s: str) -> str:
    """清洗字符串：去除非法字符并转大写，连续非法字符压缩为 '-'. """
    s = (s or "").strip()
    s = _SANITIZE_RE.sub("-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-").upper()

def iter_images(folder: Path, recursive: bool) -> Iterable[Path]:
    it = folder.rglob("*") if recursive else folder.glob("*")
    for p in it:
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            yield p

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def unique_out_dir(parent: Path, base: str) -> Path:
    """在 parent 下创建不重名的子目录 base, base-2, base-3 ..."""
    i = 1
    while True:
        name = base if i == 1 else f"{base}-{i}"
        cand = parent / name
        if not cand.exists():
            cand.mkdir(parents=True, exist_ok=False)
            return cand
        i += 1

def safe_clean_dir(target: Path) -> int:
    """安全清空 target 目录（只删除其下内容，不删除目录本身）。"""
    if not target.exists():
        return 0
    cnt = 0
    for entry in target.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            cnt += 1
        except Exception:
            pass
    return cnt

def two_phase_rename(pairs: List[Tuple[Path, Path]]) -> Tuple[int, int]:
    """两阶段改名：src->tmp，再 tmp->dst。返回(成功数, 失败数)。"""
    # 第1阶段：临时改名
    tmp_map = {}
    for src, dst in pairs:
        if not src.exists():
            continue
        tmp = src.with_name(f"__TMP__{uuid.uuid4().hex}__{src.name}")
        try:
            src.rename(tmp)
            tmp_map[tmp] = dst
        except Exception:
            # 保留原文件，进入失败统计（第2阶段会自然跳过）
            pass

    # 第2阶段：临时名 -> 目标名
    ok = 0
    fail = 0
    for tmp, dst in tmp_map.items():
        try:
            # 目标已存在则失败
            if dst.exists():
                fail += 1
                # 回滚：尽力回到原名（可能也失败）
                try:
                    orig = dst.with_name(dst.name.replace("__TMP__", ""))
                    tmp.rename(orig)
                except Exception:
                    pass
                continue
            tmp.rename(dst)
            ok += 1
        except Exception:
            fail += 1
    return ok, fail
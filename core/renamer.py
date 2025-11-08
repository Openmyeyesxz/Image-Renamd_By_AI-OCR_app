# -*- coding: utf-8 -*-
"""批量安全改名与映射CSV写入。"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Tuple

from .utils import two_phase_rename

def write_mapping_csv(csv_path: Path, rows: List[List[str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["src_dir", "old_name", "ocr_text", "base", "final_name", "status"])
        w.writerows(rows)

def perform_batch_rename(pairs: List[Tuple[Path, Path]], dry_run: bool) -> Tuple[int, int]:
    if dry_run:
        return (0, 0)
    return two_phase_rename(pairs)
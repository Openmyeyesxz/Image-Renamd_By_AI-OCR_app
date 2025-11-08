# -*- coding: utf-8 -*-
"""YOLO 推断与裁剪/合框逻辑。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
from PIL import Image

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None  # 延迟错误到运行期

# 可选：用于设备可用性判断（不存在则自动退回 CPU）
try:
    import torch
except Exception:
    torch = None

from .yolo_adapter import Detection, adapt_result


@dataclass
class YoloConfig:
    weights: Path
    device: str = "cpu"       # "cpu" 或 "cuda:0"
    imgsz: Optional[int] = None
    conf: Optional[float] = None
    iou: Optional[float] = None
    classes: Optional[Sequence[int]] = None
    use_mask_tight_bbox: bool = False


def _normalize_device(dev: str) -> str:
    """健壮化 device：无效/不可用时回退到 cpu；允许 'cuda'/'cuda:N'。"""
    if not dev:
        return "cpu"
    d = dev.strip().lower()
    if not d.startswith("cuda"):
        return "cpu"
    if torch is None or not torch.cuda.is_available():
        return "cpu"
    # 解析索引
    idx = 0
    if ":" in d:
        try:
            idx = int(d.split(":", 1)[1])
        except Exception:
            idx = 0
    try:
        _ = torch.cuda.get_device_properties(idx)  # 越界会抛错
    except Exception:
        return "cpu"
    return f"cuda:{idx}"


class Detector:
    def __init__(self, cfg: YoloConfig):
        if YOLO is None:
            raise RuntimeError("ultralytics 未安装，请先安装再运行。")

        self.cfg = cfg
        # 标准化设备（若 GPU 不可用则回退到 CPU；不会改动其它推理参数）
        self.device = _normalize_device(cfg.device)
        self.cfg.device = self.device  # 保持外部/内部一致

        # 加载模型
        self.model = YOLO(str(cfg.weights))

        # 把模型迁移到目标设备（旧版不支持 .to() 则忽略）
        try:
            self.model.to(self.device)
        except Exception:
            pass

    def predict(self, image_path: Path):
        """仅封装调用；不改任何阈值/参数。"""
        kw = {}
        if self.cfg.imgsz:
            kw["imgsz"] = self.cfg.imgsz
        if self.cfg.conf is not None:
            kw["conf"] = self.cfg.conf
        if self.cfg.iou is not None:
            kw["iou"] = self.cfg.iou
        if self.cfg.classes is not None:
            kw["classes"] = list(self.cfg.classes)

        # 兼容不同 ultralytics 版本：优先传 device，不支持时退化为不传
        try:
            results = self.model.predict(
                source=str(image_path),
                device=self.device,
                stream=False,
                **kw
            )
        except TypeError:
            # 部分旧版没有 device 参数；依赖上面的 .to()
            results = self.model.predict(
                source=str(image_path),
                stream=False,
                **kw
            )

        # Ultralytics 返回列表（每张图一个结果）
        return results or []

    def detect_for_image(self, image_path: Path) -> List[Detection]:
        results = self.predict(image_path)
        dets: List[Detection] = []
        for r in results:
            dets.extend(
                adapt_result(self.model, r, use_mask_tight_bbox=self.cfg.use_mask_tight_bbox)
            )
        return dets


def merge_boxes_xyxy(boxes: List[np.ndarray]) -> Optional[np.ndarray]:
    """合并多个 xyxy 框为一个最小外接框。"""
    if not boxes:
        return None
    arr = np.vstack(boxes)
    xmin = float(np.min(arr[:, 0]))
    ymin = float(np.min(arr[:, 1]))
    xmax = float(np.max(arr[:, 2]))
    ymax = float(np.max(arr[:, 3]))
    return np.array([xmin, ymin, xmax, ymax], dtype=np.float32)


def crop_by_xyxy(image: Image.Image, xyxy: np.ndarray) -> Image.Image:
    x1, y1, x2, y2 = [int(round(v)) for v in xyxy.tolist()]
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = max(x1 + 1, x2); y2 = max(y1 + 1, y2)
    return image.crop((x1, y1, x2, y2))

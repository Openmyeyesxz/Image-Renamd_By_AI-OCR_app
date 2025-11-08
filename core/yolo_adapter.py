# -*- coding: utf-8 -*-
"""Ultralytics YOLO 结果解析适配器（只改解析，不改模型）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Dict

import numpy as np

@dataclass
class Detection:
    cls_id: int
    cls_name: str
    conf: Optional[float]
    xyxy: np.ndarray  # (4,)
    mask: Optional[np.ndarray] = None  # (H, W) bool

def get_name_map(model) -> Dict[int, str]:
    names = getattr(model, "names", None)
    if names is None and hasattr(model, "model"):
        names = getattr(model.model, "names", None)
    if isinstance(names, dict):
        return {int(k): v for k, v in names.items()}
    if isinstance(names, (list, tuple)):
        return {i: str(n) for i, n in enumerate(names)}
    return {}

def _bbox_from_mask(mask: np.ndarray) -> Optional[np.ndarray]:
    ys, xs = np.where(mask > 0)
    if ys.size == 0 or xs.size == 0:
        return None
    ymin, ymax = ys.min(), ys.max()
    xmin, xmax = xs.min(), xs.max()
    return np.array([xmin, ymin, xmax, ymax], dtype=np.float32)

def adapt_result(model, result, use_mask_tight_bbox: bool = False) -> List[Detection]:
    """
    将 Ultralytics 的单张预测结果对象解析为统一 Detection 列表。
    不修改任何推理阈值/策略，仅处理结果的读取。
    """
    dets: List[Detection] = []
    name_map = get_name_map(model) or {}
    boxes = getattr(result, "boxes", None)
    masks = getattr(result, "masks", None)

    if boxes is None:
        return dets

    # 读取批量属性
    try:
        cls_arr = boxes.cls.detach().cpu().numpy().astype(int)
    except Exception:
        cls_arr = None
    try:
        conf_arr = getattr(boxes, "conf", None)
        if conf_arr is not None:
            conf_arr = conf_arr.detach().cpu().numpy().astype(float)
    except Exception:
        conf_arr = None
    try:
        xyxy_arr = boxes.xyxy.detach().cpu().numpy().astype(float)
    except Exception:
        xyxy_arr = None

    n = 0 if xyxy_arr is None else xyxy_arr.shape[0]
    for i in range(n):
        cls_id = int(cls_arr[i]) if cls_arr is not None else -1
        cls_name = name_map.get(cls_id, str(cls_id))
        conf = float(conf_arr[i]) if conf_arr is not None else None
        xyxy = xyxy_arr[i]

        m = None
        if masks is not None and getattr(masks, "data", None) is not None:
            try:
                m = masks.data[i].detach().cpu().numpy().astype(bool)
            except Exception:
                m = None

        if use_mask_tight_bbox and m is not None:
            tight = _bbox_from_mask(m)
            if tight is not None:
                xyxy = tight

        dets.append(Detection(cls_id=cls_id, cls_name=cls_name, conf=conf, xyxy=xyxy, mask=m))

    return dets
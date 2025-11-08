# -*- coding: utf-8 -*-
"""Ark OCR 客户端封装
- 优先尝试 volcenginesdkarkruntime（与你旧代码兼容）
- 若不可用，则使用 requests 直接调用 REST API（无需 SDK）
"""
from __future__ import annotations

import io
import os
import base64
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import requests
from PIL import Image

DEFAULT_ARK_MODEL = "doubao-1-5-thinking-vision-pro-250428"
DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

@dataclass
class ArkConfig:
    model: str = DEFAULT_ARK_MODEL
    api_key: Optional[str] = None
    base_url: str = DEFAULT_ARK_BASE_URL

class OCRClient:
    def __init__(self, cfg: ArkConfig):
        self.cfg = cfg

    @staticmethod
    def _pil_to_data_url(im: Image.Image, mime: str = "image/png") -> str:
        fmt = "PNG" if mime == "image/png" else "JPEG"
        bio = io.BytesIO()
        im.save(bio, format=fmt)
        b64 = base64.b64encode(bio.getvalue()).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    def _sdk_call(self, image: Image.Image, prompt: str, api_key: str) -> str:
        try:
            from volcenginesdkarkruntime import Ark  # 优先用你旧脚本的 SDK
        except Exception:
            return ""  # 回退到 REST
        client = Ark(base_url=self.cfg.base_url, api_key=api_key)
        data_url = self._pil_to_data_url(image, mime="image/png")
        try:
            resp = client.chat.completions.create(
                model=self.cfg.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": data_url},
                        {"type": "text", "text": prompt or ""},
                    ],
                }],
            )
            text = (resp.choices[0].message.content or "").strip()
            lines = [ln for ln in text.splitlines() if ln.strip()]
            return lines[-1] if lines else ""
        except Exception:
            return ""  # 失败继续走 REST

    def _rest_call(self, image: Image.Image, prompt: str, api_key: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data_url = self._pil_to_data_url(image, mime="image/png")
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": data_url},
                    {"type": "text", "text": prompt or ""},
                ],
            }]
        }
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            obj = r.json()
            text = (obj.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            lines = [ln for ln in text.splitlines() if ln.strip()]
            return lines[-1] if lines else ""
        except Exception as e:
            return f"[OCR错误]{e}"

    def ocr(self, image: Image.Image, prompt: str) -> str:
        api_key = self.cfg.api_key or os.environ.get("ARK_API_KEY")
        if not api_key:
            return "UNKNOWN"
        # 先试 SDK，再退 REST
        text = self._sdk_call(image, prompt, api_key)
        if text:
            return text
        return self._rest_call(image, prompt, api_key)
# -*- coding: utf-8 -*-
"""AI 识别任务页（可并发、多实例）。"""
from __future__ import annotations

# --- ensure project root on sys.path (avoid relative import issues) ---
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]  # .../qfluent_tag_app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ---------------------------------------------------------------------

import threading
import time
import torch
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Set

from PIL import Image, ImageOps

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget, QFileDialog, QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    LineEdit, ComboBox, SwitchButton, PrimaryPushButton, PushButton, InfoBar, InfoBarPosition,
    TitleLabel, StrongBodyLabel, TextEdit, ProgressBar
)

from core.utils import iter_images, ensure_dir, safe_clean_dir, sanitize_and_upper
from core.planner import collect_existing_basenames, plan_name
from core.renamer import write_mapping_csv, perform_batch_rename
from core.detector import Detector, YoloConfig, merge_boxes_xyxy, crop_by_xyxy
from core.ocr_client import OCRClient, ArkConfig
from core.yolo_adapter import Detection


@dataclass
class TaskParams:
    input_dir: Path
    # 固定“一个裁剪目录”；递归时也都存这里（可为 None 表示不保存）
    crops_dir: Optional[Path]
    save_crops: bool
    # 输出“基路径”，程序在其下为每个子目录创建 <子名>_output
    out_root: Path
    clean_out: bool
    # 模型/设备/OCR
    weights: Path
    device: str
    target_class_name: str
    ark_model: str
    ark_key: str
    prompt: str
    # 其它
    recursive: bool = False
    duplicates: bool = True
    dry_run: bool = False
    use_mask_tight_bbox: bool = False


class AiWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(int, dict)  # code, stats

    def __init__(self, params: TaskParams):
        super().__init__()
        self.params = params
        self._cancel = threading.Event()

    def request_cancel(self):
        self._cancel.set()

    def run(self):
        p = self.params
        start = time.time()
        stats = {"total": 0, "planned": 0, "renamed_ok": 0, "renamed_fail": 0}
        code = 0

        try:
            in_dir = p.input_dir

            # 1) 输出“基路径”
            out_base = p.out_root
            ensure_dir(out_base)

            # 子输出目录只清一次
            cleaned_subdirs: Set[Path] = set()

            # 2) 固定裁剪目录（可为空；若非空则所有裁剪图都放此处）
            crop_dir = None
            if p.save_crops and p.crops_dir:
                crop_dir = p.crops_dir
                ensure_dir(crop_dir)

            # 3) 模型与 OCR（不改模型参数）
            yolo = Detector(YoloConfig(weights=p.weights, device=p.device))
            ocr = OCRClient(ArkConfig(model=p.ark_model, api_key=p.ark_key))

            # 4) 列出图片
            images = list(iter_images(in_dir, p.recursive))
            total = len(images)
            stats["total"] = total
            if total == 0:
                self.log.emit("[WARN] 未找到任何图片")
                self.finished.emit(0, stats)
                return

            # 5) 各子输出目录的去重集合
            existing_map: Dict[Path, Set[str]] = {}

            rows: List[List[str]] = []
            plan_pairs: List[Tuple[Path, Path]] = []

            for idx, img_path in enumerate(images, start=1):
                if self._cancel.is_set():
                    self.log.emit("[CANCEL] 已请求终止：仍将执行已规划的改名")
                    break

                self.progress.emit(int(idx * 100 / total), f"处理 {img_path.name} ({idx}/{total})")

                # 以输入根目录为基，取第一层子目录名；非递归用输入根目录名
                try:
                    rel = img_path.parent.relative_to(in_dir)
                    first = rel.parts[0] if rel.parts else in_dir.name
                except Exception:
                    first = img_path.parent.name

                sub_out = out_base / f"{first}_output"
                if sub_out not in cleaned_subdirs:
                    ensure_dir(sub_out)
                    if p.clean_out:
                        cnt = safe_clean_dir(sub_out)
                        self.log.emit(f"[info] 已清空输出目录 {sub_out}（清理项 {cnt}）")
                    cleaned_subdirs.add(sub_out)

                if sub_out not in existing_map:
                    existing_map[sub_out] = collect_existing_basenames(sub_out)

                # 打开图像
                try:
                    im = Image.open(img_path)
                    im = ImageOps.exif_transpose(im)
                except Exception as e:
                    rows.append([str(img_path.parent), img_path.name, "", "", "", "READ_FAIL"])
                    continue

                # 检测
                dets: List[Detection] = yolo.detect_for_image(img_path)
                target_boxes = [
                    d.xyxy for d in dets
                    if d.cls_name.strip().lower() == p.target_class_name.strip().lower()
                ]
                if not target_boxes:
                    rows.append([str(img_path.parent), img_path.name, "", "", "", "NO_DET"])
                    continue

                # 合并与裁剪
                xyxy = merge_boxes_xyxy(target_boxes)
                crop = crop_by_xyxy(im, xyxy)

                # 保存裁剪（统一放一个目录；文件名加父目录前缀避免重名）
                if crop_dir is not None:
                    try:
                        stem = f"{img_path.parent.name}_{img_path.stem}_cropped"
                        crop.save(crop_dir / f"{stem}{img_path.suffix.lower()}")
                    except Exception:
                        pass

                # OCR
                ocr_text = ocr.ocr(crop, p.prompt)
                if not ocr_text or ocr_text.startswith("[OCR错误]"):
                    rows.append([str(img_path.parent), img_path.name, ocr_text or "", "", "", "NO_TEXT"])
                    continue

                base = sanitize_and_upper(ocr_text)
                final = plan_name(base, img_path.suffix.lower(), existing_map[sub_out], p.duplicates)
                dst = sub_out / final

                plan_pairs.append((img_path, dst))
                rows.append([str(img_path.parent), img_path.name, ocr_text, base, final, "PLANNED"])

            # 写 CSV（仍放在输入根目录）
            write_mapping_csv(p.input_dir / "rename_mapping.csv", rows)

            # 执行改名
            ok, fail = perform_batch_rename(plan_pairs, p.dry_run)
            stats["planned"] = len(plan_pairs)
            stats["renamed_ok"] = ok
            stats["renamed_fail"] = fail
            dur = time.time() - start

            self.log.emit(f"[OK] 任务完成；计划 {len(plan_pairs)}，成功 {ok}，失败 {fail}；用时 {dur:.1f}s")
        except Exception as e:
            self.log.emit(f"[FATAL] {e}")
            code = 1

        self.finished.emit(code, stats)


class AiTaskPage(QWidget):
    """单个 AI 识别任务的 UI。可多实例并行运行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[AiWorker] = None
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        root.addWidget(TitleLabel("AI 识别任务", self))

        # 小工具：路径行（标签 + 输入框 + 浏览按钮）
        def add_path_row(label_text: str, line_edit: LineEdit, on_browse, btn_text="浏览..."):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(StrongBodyLabel(label_text, self))
            row.addWidget(line_edit, 1)
            btn = PushButton(btn_text, self)
            btn.clicked.connect(on_browse)
            row.addWidget(btn)
            root.addLayout(row)
            return btn

        # 小工具：开关行（左标签 + 右侧小开关；去掉 On/Off 文案）
        def add_switch_row(label_text: str, switch: SwitchButton, checked: bool):
            try:
                switch.setOnText("")  # 某些版本支持
                switch.setOffText("")
            except Exception:
                pass
            switch.setChecked(checked)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(StrongBodyLabel(label_text, self))
            row.addStretch(1)
            row.addWidget(switch)
            root.addLayout(row)

        # ===== 路径类输入：按“输入 → 输出 → 裁剪”的顺序 =====
        self.in_edit = LineEdit(self)
        self.in_edit.setPlaceholderText("输入图片目录")
        add_path_row("输入目录", self.in_edit, self._choose_in)

        self.out_dir_edit = LineEdit(self)
        self.out_dir_edit.setPlaceholderText("输出基路径(程序会在其下创建 *_output 子文件夹)")
        add_path_row("输出目录", self.out_dir_edit, self._choose_out)

        self.crops_dir_edit = LineEdit(self)
        self.crops_dir_edit.setPlaceholderText("裁剪图输出目录(留空则用：输出基路径/cropped)")
        add_path_row("裁剪目录", self.crops_dir_edit, self._choose_crops)

        # 开关：清空输出（逐个 *_output）、递归子文件夹、编号去重、Dry Run
        self.clean_out_sw = SwitchButton(self)
        add_switch_row("运行前清空该目录(逐个 *_output)", self.clean_out_sw, checked=True)

        self.recursive_sw = SwitchButton(self)
        add_switch_row("递归子文件夹", self.recursive_sw, checked=False)

        self.duplicates_sw = SwitchButton(self)
        add_switch_row("标签重复编号", self.duplicates_sw, checked=True)

        self.dry_run_sw = SwitchButton(self)
        add_switch_row("演练模式(不保存)", self.dry_run_sw, checked=False)

        # 设备/类别/OCR/权重
        self.device_combo = ComboBox(self)
        self.device_combo.addItems(["cpu", "cuda:0", "cuda:1", "cuda:2", "cuda:3"])
        row_dev = QHBoxLayout();
        row_dev.addWidget(StrongBodyLabel("设备", self));
        row_dev.addWidget(self.device_combo, 1)
        root.addLayout(row_dev)

        self.target_edit = LineEdit(self);
        self.target_edit.setText("WhiteTag")
        row_target = QHBoxLayout();
        row_target.addWidget(StrongBodyLabel("目标类名", self));
        row_target.addWidget(self.target_edit, 1)
        root.addLayout(row_target)

        self.ark_model_edit = LineEdit(self);
        self.ark_model_edit.setText("doubao-1-5-thinking-vision-pro-250428")
        row_am = QHBoxLayout();
        row_am.addWidget(StrongBodyLabel("Ark 模型", self));
        row_am.addWidget(self.ark_model_edit, 1)
        root.addLayout(row_am)

        self.ark_key_edit = LineEdit(self)
        row_key = QHBoxLayout();
        row_key.addWidget(StrongBodyLabel("Ark 密钥", self));
        row_key.addWidget(self.ark_key_edit, 1)
        root.addLayout(row_key)

        self.prompt_edit = LineEdit(self)
        row_prompt = QHBoxLayout();
        row_prompt.addWidget(StrongBodyLabel("OCR 提示词", self));
        row_prompt.addWidget(self.prompt_edit, 1)
        root.addLayout(row_prompt)

        self.weights_edit = LineEdit(self)
        self.weights_edit.setPlaceholderText("模型权重(.pt/.pth)")
        add_path_row("模型权重", self.weights_edit, self._choose_weights)

        # 控制按钮
        ctrl = QHBoxLayout()
        self.btn_clear_crops = PushButton("清空裁剪目录", self)
        self.btn_start = PrimaryPushButton("开始", self)
        self.btn_stop = PushButton("停止", self);
        self.btn_stop.setEnabled(False)
        for b in (self.btn_clear_crops, self.btn_start, self.btn_stop):
            ctrl.addWidget(b)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # 进度与日志
        self.progress = ProgressBar(self);
        self.progress.setValue(0);
        root.addWidget(self.progress)
        self.log_edit = TextEdit(self);
        self.log_edit.setReadOnly(True);
        self.log_edit.setMinimumHeight(220);
        root.addWidget(self.log_edit, 1)

        # 信号
        self.btn_clear_crops.clicked.connect(self._clear_crops)
        self.btn_start.clicked.connect(self._start)
        self.btn_stop.clicked.connect(self._stop)
        self.out_dir_edit.textChanged.connect(self._sync_crops_hint)

    # ---------- 工具 for 外部控制 ----------
    def is_running(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    def stop_and_wait(self, ms: int = 2000):
        if self._worker:
            try:
                self._worker.request_cancel()
                self._worker.wait(ms)
            except Exception:
                pass
            finally:
                self.btn_start.setEnabled(True)
                self.btn_stop.setEnabled(False)
                self._worker = None

    # ---------- 事件 ----------
    def _sync_crops_hint(self):
        text = self.out_dir_edit.text().strip()
        if text and not self.crops_dir_edit.text().strip():
            # 仅当用户未手填裁剪目录时，给出默认提示
            self.crops_dir_edit.setText(str(Path(text) / "cropped"))

    def _choose_in(self):
        d = QFileDialog.getExistingDirectory(self, "选择输入目录")
        if d:
            self.in_edit.setText(d)

    def _choose_crops(self):
        d = QFileDialog.getExistingDirectory(self, "选择裁剪图输出目录")
        if d:
            self.crops_dir_edit.setText(d)

    def _choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出根目录")
        if d:
            self.out_dir_edit.setText(d)
            self._sync_crops_hint()

    def _choose_weights(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择模型权重文件", filter="PyTorch (*.pt *.pth);;All (*)")
        if f:
            self.weights_edit.setText(f)

    def _clear_crops(self):
        base_txt = self.crops_dir_edit.text().strip()
        if not base_txt:
            InfoBar.warning("提醒", "未填写裁剪目录", position=InfoBarPosition.TOP_RIGHT, parent=self)
            return

        base_path = Path(base_txt)

        # 只清理 cropped 子目录：如果用户直接填了 .../cropped 就用它；否则指向 base_path/cropped
        crop_dir = base_path if base_path.name.lower() == "cropped" else (base_path / "cropped")

        if not crop_dir.exists():
            InfoBar.success("提示", f"目标文件夹不存在：{crop_dir}，无需清空", position=InfoBarPosition.TOP_RIGHT,
                            parent=self)
            return
        if not crop_dir.is_dir():
            InfoBar.error("错误", f"目标不是文件夹：{crop_dir}", position=InfoBarPosition.TOP_RIGHT, parent=self)
            return

        cleared = 0
        for x in crop_dir.iterdir():  # 只删除 cropped 内部内容，不删除 cropped 本身
            try:
                if x.is_file() or x.is_symlink():
                    x.unlink()
                else:
                    import shutil
                    shutil.rmtree(x)
                cleared += 1
            except Exception:
                pass

        InfoBar.success("完成", f"已清空 {crop_dir} 内部内容（{cleared} 项）",
                        position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _append_log(self, s: str):
        self.log_edit.append(s)

    def _on_progress(self, pct: int, msg: str):
        self.progress.setValue(max(0, min(100, pct)))
        if msg:
            self._append_log(msg)

    def _on_finished(self, code: int, stats: dict):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if code == 0:
            InfoBar.success("完成", "AI 识别任务完成", position=InfoBarPosition.TOP_RIGHT, parent=self)
        else:
            InfoBar.error("失败", "AI 识别任务失败，详见日志", position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _start(self):

        # 读取下拉框的设备
        dev = self.device_combo.currentText().strip()

        # 若用户选了 CUDA，但当前环境/索引不可用，则回退到 CPU
        if dev.startswith("cuda"):
            try:
                if not torch.cuda.is_available():
                    InfoBar.warning("GPU 不可用", "已自动切换到 CPU",
                                    position=InfoBarPosition.TOP_RIGHT, parent=self)
                    dev = "cpu"
                else:
                    # 简单校验 cuda:N 是否存在
                    try:
                        idx = int(dev.split(":")[1])
                        _ = torch.cuda.get_device_properties(idx)  # 索引越界会抛错
                    except Exception:
                        InfoBar.warning("GPU 设备无效", f"找不到 {dev}，已切换到 CPU",
                                        position=InfoBarPosition.TOP_RIGHT, parent=self)
                        dev = "cpu"
            except Exception:
                # torch 导入/查询异常，保险起见退回 CPU
                dev = "cpu"

        self._append_log(f"[INFO] 使用设备：{dev}")
        in_dir = Path(self.in_edit.text().strip())
        if not in_dir.exists():
            InfoBar.error("错误", "输入目录不存在", position=InfoBarPosition.TOP_RIGHT, parent=self)
            return

        out_base = Path(self.out_dir_edit.text().strip())
        if not out_base:
            InfoBar.error("错误", "请填写【输出基路径】", position=InfoBarPosition.TOP_RIGHT, parent=self)
            return
        ensure_dir(out_base)

        weights = Path(self.weights_edit.text().strip())
        if not weights.exists():
            InfoBar.error("错误", "模型权重不存在", position=InfoBarPosition.TOP_RIGHT, parent=self)
            return

        # 始终保存裁剪：用户未填时默认使用 输出基路径/cropped
        crops_dir_txt = self.crops_dir_edit.text().strip()

        # === 始终写入 cropped 子目录（关键逻辑） ===
        crops_dir_txt = self.crops_dir_edit.text().strip()
        if crops_dir_txt:
            cd = Path(crops_dir_txt)
            crops_dir = cd if cd.name.lower() == "cropped" else (cd / "cropped")
        else:
            crops_dir = out_base / "cropped"
        ensure_dir(crops_dir)

        params = TaskParams(
            input_dir=in_dir,
            crops_dir=crops_dir,
            save_crops=True,
            out_root=out_base, #统一字段名
            clean_out=self.clean_out_sw.isChecked(),
            weights=weights,
            device=dev,
            target_class_name=self.target_edit.text().strip() or "WhiteTag",
            ark_model=self.ark_model_edit.text().strip() or "doubao-1-5-thinking-vision-pro-250428",
            ark_key=self.ark_key_edit.text().strip(),
            prompt=self.prompt_edit.text().strip(),
            recursive=self.recursive_sw.isChecked(),
            duplicates=self.duplicates_sw.isChecked(),
            dry_run=self.dry_run_sw.isChecked(),
            use_mask_tight_bbox=False,
        )

        self._worker = AiWorker(params)
        self._worker.log.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._append_log(f"[INFO] 任务已启动：{in_dir} -> {out_base}")

    def _stop(self):
        if self._worker:
            self._worker.request_cancel()
            self._append_log("[INFO] 已请求终止(将保留已规划的改名执行)")

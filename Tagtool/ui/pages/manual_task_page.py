# -*- coding: utf-8 -*-
"""人工核验任务页（按新规则）
- 前缀 + 中间项 直接相连
- “编号”改“后缀”：下拉仅显示数字，生成时自动拼接成 -<数字>
- “后缀自定义”：文本框 + 沿用 + 启用开关；启用后以自定义内容为准（不自动加任何字符），并禁用“后缀(下拉)”
- 预览文件名为只读文本
"""
from __future__ import annotations

# --- 保证 project root 在 sys.path ---
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ------------------------------------

from typing import List, Optional
from PIL import Image, ImageOps, ImageQt

from PyQt6.QtCore import Qt, QSize, QTimer, QSettings
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence, QFont
from PyQt6.QtWidgets import (
    QWidget, QFileDialog, QHBoxLayout, QVBoxLayout, QLabel,
    QSizePolicy, QFrame, QGridLayout, QSplitter
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, LineEdit, CheckBox, ComboBox, SwitchButton,
    StrongBodyLabel, BodyLabel, TitleLabel, InfoBar, InfoBarPosition
)

try:
    import psutil
except Exception:
    psutil = None

from core.utils import iter_images, sanitize_and_upper

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


class ManualTaskPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._root: Optional[Path] = None
        self._images: List[Path] = []
        self._cur: int = -1

        # “沿用”记忆
        self._keep_prefix = False
        self._keep_middle = False
        self._keep_suffix_num = False        # 针对“后缀(下拉)”
        self._keep_suffix_custom = False     # 针对“后缀自定义”

        # 保存分隔条尺寸的定时器（防抖保存）
        self._splitter_timer = QTimer(self)
        self._splitter_timer.setSingleShot(True)
        self._splitter_timer.setInterval(300)
        self._splitter_timer.timeout.connect(self._save_splitter_sizes)

        self._build_ui()
        self._bind_shortcuts()
        self._start_sysmon()

    # ---------- UI ----------
    def _build_ui(self):

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 顶部工具条
        tool = QHBoxLayout()
        self.btn_choose_root = PushButton("选择图片根目录", self)
        self.cb_recursive = CheckBox("递归子文件夹", self)
        self.btn_refresh = PushButton("刷新列表", self)
        self.lbl_counter = BodyLabel("0/0", self)
        self.lbl_counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tool.addWidget(self.btn_choose_root)
        tool.addWidget(self.cb_recursive)
        tool.addWidget(self.btn_refresh)
        tool.addStretch(1)
        tool.addWidget(self.lbl_counter)
        root.addLayout(tool)

        # ===== 中部用 QSplitter：左预览 + 右工作台 =====
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter = splitter  # 保存引用供后续保存/恢复

        # -------- 左侧（文件名 + 预览图）--------
        left_wrap = QWidget(self)
        left = QVBoxLayout(left_wrap)
        left.setSpacing(6)

        self.raw_name = TitleLabel("", self)
        self.raw_name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        left.addWidget(self.raw_name)

        self.image_box = QLabel(self)
        self.image_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_box.setFrameShape(QFrame.Shape.StyledPanel)
        # 让左侧更容易收缩，右侧优先完整显示
        self.image_box.setMinimumSize(QSize(400, 300))
        self.image_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left.addWidget(self.image_box, 1)

        splitter.addWidget(left_wrap)

        # -------- 右侧（工作台）--------
        right_wrap = QFrame(self)
        right_wrap.setFrameShape(QFrame.Shape.StyledPanel)
        right_wrap.setStyleSheet("QFrame{border:1px solid rgba(0,0,0,0.12); border-radius:6px;}")
        right_wrap.setMinimumWidth(360)

        right = QVBoxLayout(right_wrap)
        right.setContentsMargins(10, 10, 10, 10)
        right.setSpacing(8)

        # 竖直显示
        self.sw_force_vertical = CheckBox("强制竖直显示（宽>高时旋转90°）", self)
        right.addWidget(self.sw_force_vertical)

        # 三段式（但按你的规则组合）：前缀、中间项、后缀（数字）/ 后缀自定义（优先）
        form = QGridLayout()
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(6)

        # 前缀
        form.addWidget(StrongBodyLabel("前缀：", self), 0, 0)
        self.edit_prefix = LineEdit(self)
        self.cb_keep_prefix = CheckBox("沿用", self)
        form.addWidget(self.edit_prefix, 0, 1)
        form.addWidget(self.cb_keep_prefix, 0, 2)

        # 中间项
        form.addWidget(StrongBodyLabel("中间项：", self), 1, 0)
        self.edit_middle = LineEdit(self)
        self.cb_keep_middle = CheckBox("沿用", self)
        form.addWidget(self.edit_middle, 1, 1)
        form.addWidget(self.cb_keep_middle, 1, 2)

        # 后缀（数字下拉）行：展示纯数字；生成时自动拼接成 -<数字>
        form.addWidget(StrongBodyLabel("后缀：", self), 2, 0)
        self.combo_suffix_num = ComboBox(self)
        self.combo_suffix_num.addItems(["", "1", "2", "3"])
        self.cb_keep_suffix_num = CheckBox("沿用", self)
        form.addWidget(self.combo_suffix_num, 2, 1)
        form.addWidget(self.cb_keep_suffix_num, 2, 2)

        # 后缀自定义（优先级更高）：文本框 + 沿用 + 启用
        form.addWidget(StrongBodyLabel("后缀自定义：", self), 3, 0)
        row_custom = QHBoxLayout()
        self.edit_suffix_custom = LineEdit(self)
        self.edit_suffix_custom.setPlaceholderText("启用后，按原样拼接，不自动加字符")
        self.cb_keep_suffix_custom = CheckBox("沿用", self)
        self.sw_suffix_custom_enable = SwitchButton("启用", self)
        row_custom.addWidget(self.edit_suffix_custom, 1)
        row_custom.addWidget(self.cb_keep_suffix_custom)
        row_custom.addWidget(self.sw_suffix_custom_enable)
        form.addLayout(row_custom, 3, 1, 1, 2)

        # 预览文件名（只读文本直显）
        form.addWidget(StrongBodyLabel("预览文件名：", self), 4, 0)
        self.preview_value = BodyLabel("", self)
        self.preview_value.setWordWrap(False)
        self.preview_value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preview_value.setFixedHeight(28)
        f = QFont(); f.setPixelSize(14)
        self.preview_value.setFont(f)
        self.preview_value.setStyleSheet(
            "QLabel{background:transparent;border:none;padding-left:6px;padding-right:6px;}"
        )
        form.addWidget(self.preview_value, 4, 1, 1, 2)

        right.addLayout(form)

        # 操作按钮
        btns = QGridLayout(); btns.setHorizontalSpacing(8); btns.setVerticalSpacing(8)
        self.btn_prev = PushButton("← 上一个", self)
        self.btn_next = PushButton("下一个 →", self)
        self.btn_pass_next = PushButton("通过（不改）并下一张（方向键）", self)
        self.btn_save_next = PrimaryPushButton("保存并下一张（回车）", self)
        self.btn_clear_inputs = PushButton("清空输入", self)
        btns.addWidget(self.btn_prev,        0, 0)
        btns.addWidget(self.btn_next,        0, 1)
        btns.addWidget(self.btn_pass_next,   1, 0, 1, 2)
        btns.addWidget(self.btn_save_next,   2, 0, 1, 2)
        btns.addWidget(self.btn_clear_inputs,3, 0, 1, 2)
        right.addLayout(btns)

        # 状态（只文字）
        st = QHBoxLayout()
        self.lbl_status = BodyLabel("状态：未加载", self)
        f2 = QFont(); f2.setPixelSize(13)
        self.lbl_status.setFont(f2)
        self.lbl_status.setWordWrap(False)
        self.lbl_status.setStyleSheet("QLabel{background:transparent;border:none;}")
        st.addWidget(self.lbl_status)
        st.addStretch(1)
        right.addLayout(st)

        right.addStretch(1)

        splitter.addWidget(right_wrap)

        # 初始左右宽度（若无历史记录）
        splitter.setSizes([800, 520])

        # 分隔条移动后 300ms 保存
        splitter.splitterMoved.connect(lambda *_: self._splitter_timer.start())

        # 将分隔控件加入页面
        root.addWidget(splitter, 1)

        # 页面最小尺寸
        self.setMinimumSize(900, 600)

        # 恢复上次拖拽宽度
        self._restore_splitter_sizes()

        # 底部：进度 + 系统信息
        bottom = QHBoxLayout()
        self.lbl_progress = BodyLabel("进度：0/0", self)
        self.lbl_sys = BodyLabel("", self)
        bottom.addWidget(self.lbl_progress)
        bottom.addStretch(1)
        bottom.addWidget(self.lbl_sys)
        root.addLayout(bottom)

        # 信号
        self.btn_choose_root.clicked.connect(self._choose_root)
        self.btn_refresh.clicked.connect(self._refresh_list)

        self.cb_keep_prefix.stateChanged.connect(self._sync_keep_flags)
        self.cb_keep_middle.stateChanged.connect(self._sync_keep_flags)
        self.cb_keep_suffix_num.stateChanged.connect(self._sync_keep_flags)
        self.cb_keep_suffix_custom.stateChanged.connect(self._sync_keep_flags)

        self.edit_prefix.textChanged.connect(self._update_preview)
        self.edit_middle.textChanged.connect(self._update_preview)
        self.combo_suffix_num.currentTextChanged.connect(self._update_preview)
        self.edit_suffix_custom.textChanged.connect(self._update_preview)
        self.sw_suffix_custom_enable.checkedChanged.connect(self._on_toggle_suffix_mode)

        self.btn_prev.clicked.connect(self.prev)
        self.btn_next.clicked.connect(self.next)
        self.btn_pass_next.clicked.connect(self.pass_and_next)
        self.btn_save_next.clicked.connect(self.save_and_next)
        self.btn_clear_inputs.clicked.connect(self.clear_inputs)

        # 初始化开关状态
        self._on_toggle_suffix_mode(self.sw_suffix_custom_enable.isChecked())

        # 页面销毁时再保存一次分隔条尺寸（防止最后一次拖拽没触发定时器）
        self.destroyed.connect(lambda *_: self._save_splitter_sizes())

    # ---------- 分隔条尺寸的保存/恢复 ----------
    def _settings(self) -> 'QSettings':
        from PyQt6.QtCore import QSettings
        return QSettings("TagTool", "ManualTaskPage")

    def _save_splitter_sizes(self):
        try:
            s = self._settings()
            s.setValue("splitterSizes", self.splitter.sizes())
        except Exception:
            pass

    def _restore_splitter_sizes(self):
        try:
            s = self._settings()
            sizes = s.value("splitterSizes", None)
            if sizes:
                self.splitter.setSizes([int(x) for x in list(sizes)])
        except Exception:
            pass

    # ---------- 快捷键 ----------
    def _bind_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Left),  self, activated=self.prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self.pass_and_next)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self.save_and_next)
        QShortcut(QKeySequence(Qt.Key.Key_Enter),  self, activated=self.save_and_next)

    # ---------- 系统监控 ----------
    def _start_sysmon(self):
        if not psutil:
            self.lbl_sys.setText("")
            return
        t = QTimer(self)
        t.timeout.connect(self._tick_sys)
        t.start(1000)
        self._sys_timer = t

    def _tick_sys(self):
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            used = int((mem.total - mem.available) / (1024**2))
            total = int(mem.total / (1024**2))
            self.lbl_sys.setText(f"CPU {int(cpu)}% | 内存 {used}M/{total}M")
        except Exception:
            pass

    # ---------- 业务 ----------
    def _choose_root(self):
        d = QFileDialog.getExistingDirectory(self, "选择图片根目录")
        if not d:
            return
        self._root = Path(d)
        self._refresh_list()

    def _refresh_list(self):
        if not self._root:
            InfoBar.warning("提示", "请先选择图片根目录", position=InfoBarPosition.TOP_RIGHT, parent=self)
            return
        recursive = self.cb_recursive.isChecked()
        self._images = [p for p in iter_images(self._root, recursive) if p.suffix.lower() in IMG_EXTS]
        self._images.sort()
        self._cur = 0 if self._images else -1
        self.lbl_counter.setText(f"{0 if self._cur < 0 else 1}/{len(self._images)}")
        self.lbl_progress.setText(f"进度：{0 if self._cur < 0 else 1}/{len(self._images)}")
        self._load_current()

    def _sync_keep_flags(self):
        self._keep_prefix = self.cb_keep_prefix.isChecked()
        self._keep_middle = self.cb_keep_middle.isChecked()
        self._keep_suffix_num = self.cb_keep_suffix_num.isChecked()
        self._keep_suffix_custom = self.cb_keep_suffix_custom.isChecked()

    def _on_toggle_suffix_mode(self, enabled: bool):
        """启用自定义后缀 → 以自定义为准，禁用数字下拉；关闭则相反"""
        self.edit_suffix_custom.setEnabled(enabled)
        self.combo_suffix_num.setEnabled(not enabled)
        self._update_preview()

    def _compose_base(self) -> str:
        """根据当前规则组合 base 名（不含扩展名）"""
        prefix = self.edit_prefix.text().strip()
        middle = self.edit_middle.text().strip()
        head = f"{prefix}{middle}"  # 前缀 + 中间项 直接相连
        if self.sw_suffix_custom_enable.isChecked():
            suffix = self.edit_suffix_custom.text().strip()
        else:
            num = self.combo_suffix_num.currentText().strip()
            suffix = f"-{num}" if num else ""
        return f"{head}{suffix}"

    def _update_preview(self):
        self.preview_value.setText(sanitize_and_upper(self._compose_base()))

    def _render_current_pixmap(self, pix: QPixmap):
        if not pix:
            self.image_box.setPixmap(QPixmap())
            return
        self.image_box.setPixmap(
            pix.scaled(self.image_box.size(), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        )

    def _load_current(self):
        if self._cur < 0 or self._cur >= len(self._images):
            self.raw_name.setText("")
            self.image_box.setPixmap(QPixmap())
            self.lbl_status.setText("状态：未加载")
            return

        p = self._images[self._cur]
        self.raw_name.setText(p.name)
        self.lbl_counter.setText(f"{self._cur+1}/{len(self._images)}")
        self.lbl_progress.setText(f"进度：{self._cur+1}/{len(self._images)}")

        # 切图时按“沿用”策略清空
        if not self._keep_prefix:
            self.edit_prefix.setText("")
        if not self._keep_middle:
            self.edit_middle.setText("")
        if self.sw_suffix_custom_enable.isChecked():
            if not self._keep_suffix_custom:
                self.edit_suffix_custom.setText("")
        else:
            if not self._keep_suffix_num:
                self.combo_suffix_num.setCurrentIndex(0)

        # 加载并显示
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im)
                if self.sw_force_vertical.isChecked() and im.width > im.height:
                    im = im.rotate(90, expand=True)
                qimg = ImageQt.ImageQt(im.copy())
            self._render_current_pixmap(QPixmap.fromImage(qimg))
            self.lbl_status.setText("状态：已加载")
        except Exception as e:
            self.image_box.setPixmap(QPixmap())
            self.lbl_status.setText(f"状态：读取失败：{e}")

        self._update_preview()

    # 调整窗口时，按控件尺寸重新缩放当前 pixmap
    def resizeEvent(self, e):
        super().resizeEvent(e)
        pm = self.image_box.pixmap()
        if pm:
            self._render_current_pixmap(pm)

    # 导航与操作
    def prev(self):
        if self._cur <= 0:
            return
        self._cur -= 1
        self._load_current()

    def next(self):
        if self._cur < 0:
            return
        if self._cur + 1 < len(self._images):
            self._cur += 1
            self._load_current()

    def pass_and_next(self):
        self.lbl_status.setText("状态：通过（不改）")
        self.next()

    def save_and_next(self):
        if self._cur < 0:
            return
        src = self._images[self._cur]
        base = sanitize_and_upper(self._compose_base())

        if base:
            dst = src.with_name(f"{base}{src.suffix.lower()}")
            if dst.exists() and dst != src:
                n = 2
                while True:
                    cand = src.with_name(f"{base}-{n}{src.suffix.lower()}")
                    if not cand.exists():
                        dst = cand
                        break
                    n += 1
            try:
                src.rename(dst)
                self._images[self._cur] = dst
                self.lbl_status.setText(f"状态：重命名成功 → {dst.name}")
            except Exception as e:
                self.lbl_status.setText(f"状态：重命名失败：{e}")
        else:
            self.lbl_status.setText("状态：未填写任何命名段，保持原名")

        self.next()

    def clear_inputs(self):
        if not self._keep_prefix:
            self.edit_prefix.setText("")
        if not self._keep_middle:
            self.edit_middle.setText("")
        if self.sw_suffix_custom_enable.isChecked():
            if not self._keep_suffix_custom:
                self.edit_suffix_custom.setText("")
        else:
            if not self._keep_suffix_num:
                self.combo_suffix_num.setCurrentIndex(0)
        self._update_preview()

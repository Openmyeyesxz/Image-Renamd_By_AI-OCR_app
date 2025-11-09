# -*- coding: utf-8 -*-
"""左侧窄导航 + 右侧可关闭 TabBar（多任务并行）"""
from __future__ import annotations

# ---- 保证 core/ 与 ui/ 可导入 ----
import sys
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ----------------------------------

from PyQt6.QtCore import Qt, QSettings, QByteArray
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QTabWidget
from PyQt6.QtGui import QIcon

from qfluentwidgets import (
    NavigationInterface, NavigationItemPosition, FluentIcon, setTheme, Theme, MessageBox
)

from ui.pages.ai_task_page import AiTaskPage
from ui.pages.manual_task_page import ManualTaskPage


# ===== 资源定位（打包安全）=====
def _base_dir() -> Path:
    # 打包后：exe 所在目录；源码运行：当前文件目录
    return Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) \
           else Path(__file__).resolve().parent

def res_path(rel: str) -> Path:
    return (_base_dir() / rel).resolve()

def get_app_icon() -> QIcon | None:
    # 依次尝试 .ico / .png（按需可补 .icns）
    candidates = [
        "resource/images/Tagtool.ico",
        "resource/images/Tagtool.png",
        "resource/Tagtool.ico",
        "resource/Tagtool.png",
    ]
    for rel in candidates:
        p = res_path(rel)
        if p.is_file():
            return QIcon(str(p))
    return None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tag Tool")
        setTheme(Theme.AUTO)

        # ======= 左侧导航 + 右侧 TabWidget =======
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        NAV_WIDTH = 60
        self.nav = NavigationInterface(self, showMenuButton=True, showReturnButton=False)
        self.nav.setObjectName("leftNav")
        try:
            self.nav.setFixedWidth(NAV_WIDTH)
        except Exception:
            pass
        self.nav.setStyleSheet(f"#leftNav {{ min-width:{NAV_WIDTH}px; max-width:{NAV_WIDTH}px; }}")
        if hasattr(self.nav, "setCollapseWidth"):
            try:
                self.nav.setCollapseWidth(NAV_WIDTH)
            except Exception:
                pass

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabCloseRequested.connect(self._close_tab)

        layout.addWidget(self.nav)
        layout.addWidget(self.tabs, 1)
        layout.setStretchFactor(self.nav, 0)

        # 兼容不同 addItem 签名
        def add_nav_item(route_key: str, icon, text: str, on_click, position=NavigationItemPosition.TOP):
            try:
                self.nav.addItem(route_key, icon, text, on_click, position=position, tooltip=text)
            except TypeError:
                try:
                    self.nav.addItem(icon=icon, text=text, onClick=on_click,
                                     routeKey=route_key, position=position, tooltip=text)
                except TypeError:
                    self.nav.addItem(icon, text, on_click)

        add_nav_item("new_ai", FluentIcon.ADD, "AI", self._new_ai_task)
        add_nav_item("new_manual", FluentIcon.EDIT, "Manual", self._new_manual_task)

        # 窗口/自定义标题栏图标
        self._apply_window_icon()

        # 尺寸与居中（记住上次）
        self.setMinimumSize(1000, 600)
        self._apply_initial_geometry()

    # ---- 设定窗口/标题栏图标 ----
    def _apply_window_icon(self):
        icon = get_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)  # 窗口左上角/任务栏
            tb = getattr(self, "titleBar", None)
            if tb and hasattr(tb, "setIcon"):
                try:
                    tb.setIcon(icon)  # QFluentWidgets 自定义标题栏
                except Exception:
                    pass

    # ---- 初始/记忆几何 ----
    def _apply_initial_geometry(self):
        settings = QSettings("TagTool", "MainWindow")
        geo = settings.value("geometry", None, type=QByteArray)
        if geo:
            self.restoreGeometry(geo)
            return

        screen = self.screen() or QApplication.primaryScreen()
        ag = screen.availableGeometry()
        INIT_W_PCT, INIT_H_PCT = 0.75, 0.62
        MAX_W, MAX_H = 1500, 880
        w = min(max(int(ag.width()  * INIT_W_PCT), self.minimumWidth()),  MAX_W)
        h = min(max(int(ag.height() * INIT_H_PCT), self.minimumHeight()), MAX_H)
        self.resize(w, h)
        frame = self.frameGeometry()
        frame.moveCenter(ag.center())
        self.move(frame.topLeft())

    def closeEvent(self, e):
        try:
            running_pages = []
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                if isinstance(w, AiTaskPage) and w.is_running():
                    running_pages.append(w)

            if running_pages:
                box = MessageBox("确认退出",
                                 f"检测到 {len(running_pages)} 个任务仍在运行。\n退出将终止这些任务并关闭程序，是否继续？",
                                 self)
                box.yesButton.setText("继续并终止")
                box.cancelButton.setText("取消")
                if not box.exec():
                    e.ignore()
                    return
                for w in running_pages:
                    w.stop_and_wait(3000)

            # 保存几何
            s = QSettings("TagTool", "MainWindow")
            s.setValue("geometry", self.saveGeometry())

            # 释放所有页签
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                self.tabs.removeTab(0)
                w.deleteLater()
        except Exception:
            pass
        super().closeEvent(e)

    # ========== 创建页签 ==========
    def _next_seq(self, prefix: str) -> int:
        used = set()
        for i in range(self.tabs.count()):
            t = self.tabs.tabText(i)
            m = re.match(rf"^{re.escape(prefix)}\s*(\d+)$", t)
            if m:
                used.add(int(m.group(1)))
        n = 1
        while n in used:
            n += 1
        return n

    def _new_ai_task(self):
        page = AiTaskPage(self)
        seq = self._next_seq("AI")
        idx = self.tabs.addTab(page, f"AI{seq}")
        self.tabs.setCurrentIndex(idx)

    def _new_manual_task(self):
        page = ManualTaskPage(self)
        seq = self._next_seq("Manual")
        idx = self.tabs.addTab(page, f"Manual{seq}")
        self.tabs.setCurrentIndex(idx)

    # ========== 关闭页签（带确认 & 真正终止该页线程）==========
    def _close_tab(self, index: int):
        w = self.tabs.widget(index)
        if isinstance(w, AiTaskPage) and w.is_running():
            box = MessageBox("确认关闭", "该 AI 任务仍在运行，关闭将终止此任务。\n是否继续？", self)
            box.yesButton.setText("继续并终止")
            box.cancelButton.setText("取消")
            if not box.exec():
                return
            w.stop_and_wait(3000)  # 终止该页线程
        self.tabs.removeTab(index)
        w.deleteLater()  # ← 释放页面对象


if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    # 统一 QSettings 归属（可选）
    app.setOrganizationName("TagTool")
    app.setApplicationName("TagTool")

    # 应用级默认图标（影响任务栏/新窗口/部分对话框）
    _icon = get_app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)
    else:
        print(f"[WARN] icon not found among candidates")

    w = MainWindow()
    w.show()
    sys.exit(app.exec())

# manual_app.py
from __future__ import annotations
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QByteArray
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtGui import QIcon
from qfluentwidgets import setTheme, Theme

# ---- 路径准备 ----
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from ui.manual_task_page import ManualTaskPage  # 兜底

def res_path(rel: str) -> str:
    """开发环境与 Nuitka (standalone/onefile) 下均可找到资源"""
    p = ROOT / rel
    if p.exists():
        return str(p)
    exe_dir = Path(sys.argv[0]).resolve().parent
    return str(exe_dir / rel)

class ManualWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TagRename")
        setTheme(Theme.AUTO)

        # 标题栏/任务栏图标
        app_icon = QIcon(res_path("resource/images/Tagtool.ico"))
        self.setWindowIcon(app_icon)
        if hasattr(self, "titleBar"):
            try:
                self.titleBar.setIcon(app_icon)
            except Exception:
                pass

        self._restore_geometry()

        # 中心页：人工核验
        self.page = ManualTaskPage(self)
        self.setCentralWidget(self.page)
        self.setMinimumSize(900, 600)

    def _restore_geometry(self):
        s = QSettings("TagToolManual", "MainWindow")
        ba = s.value("geometry", None, type=QByteArray)
        if ba:
            self.restoreGeometry(ba)
        else:
            self.resize(1100, 720)

    def closeEvent(self, e):
        s = QSettings("TagToolManual", "MainWindow")
        s.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)

if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setOrganizationName("TagToolManual")
    app.setApplicationName("TagToolManual")

    w = ManualWindow()
    w.show()

    # 可选：命令行带入起始目录
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.is_dir():
            # 若页面实现了更合理的接口可改为 w.page.setRoot(p)
            w.page._root = p
            if hasattr(w.page, "_refresh_list"):
                w.page._refresh_list()

    sys.exit(app.exec())

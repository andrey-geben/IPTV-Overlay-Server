# main.py
import sys
import os
import ctypes
import multiprocessing
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from gui import MainWindow


APP_ID = "andreygeben.iptvoverlay.server.1.0"


def _setup_logging():
    """
    Перенаправляет stdout/stderr в файл.
    В --windowed сборке они по умолчанию None, и любая библиотека,
    которая пишет в stdout (Uvicorn, logging), молча падает.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent

    log_path = base / "app.log"
    try:
        # buffering=1 — построчная запись, чтобы лог был актуален в реальном времени
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
        print("\n===== APP START =====")
    except Exception:
        # Если и это не удалось — работаем как есть
        pass


def set_windows_app_id():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        print(f"[main] Не удалось установить AppUserModelID: {e}")


def ensure_icon() -> QIcon:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    icon_path = base / "assets" / "icon.ico"

    if not icon_path.exists():
        try:
            from make_icon import generate_icon
            generated = generate_icon(Path(__file__).parent)
            print(f"[main] Иконка сгенерирована: {generated}")
        except Exception as e:
            print(f"[main] Не удалось сгенерировать иконку: {e}")

    if icon_path.exists():
        return QIcon(str(icon_path))
    return QIcon()


def main():
    multiprocessing.freeze_support()
    _setup_logging()          # ← ДО всего остального
    set_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("IPTV Overlay Server")
    app.setApplicationVersion("1.1.0")
    app.setOrganizationName("andrey-geben")

    icon = ensure_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
# main.py
import sys
import ctypes
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from gui import MainWindow


APP_ID = "andreygeben.iptvoverlay.server.1.0"


def set_windows_app_id():
    """
    Windows показывает иконку процесса (Python), если у приложения
    не установлен AppUserModelID. Устанавливаем свой — тогда на панели
    задач и в диспетчере будет наша иконка.
    Вызывать ДО создания QApplication.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        print(f"[main] Не удалось установить AppUserModelID: {e}")


def ensure_icon() -> QIcon:
    """Возвращает иконку; при отсутствии файла — генерирует на лету."""
    icon_path = Path(__file__).parent / "assets" / "icon.ico"
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
    set_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("IPTV Overlay Server")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("andrey-geben")

    icon = ensure_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)  # глобально для всех окон

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
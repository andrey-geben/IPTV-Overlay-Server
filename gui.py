# gui.py
import os
import socket
import ipaddress
import traceback
import threading
import ctypes
import ctypes.wintypes as wintypes
from pathlib import Path

import uvicorn
from PyQt6.QtCore import Qt, QTime, QDate
from PyQt6.QtGui import QColor, QAction, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QLineEdit, QLabel,
    QListWidget, QListWidgetItem, QTimeEdit, QGroupBox,
    QAbstractItemView, QApplication, QSpinBox, QCheckBox,
    QGridLayout, QColorDialog, QDateEdit, QScrollArea,
    QDialog, QTextBrowser, QComboBox, QInputDialog
)

from playlist_manager import PlaylistManager
from stream_engine import StreamEngine
from scheduler import OverlayScheduler
from models import Overlay
from server import create_app
from ffmpeg_manager import FFmpegManager


APP_NAME = "IPTV Overlay Server"
APP_VERSION = "1.1.0"
APP_AUTHOR = "Andrey Geben"
APP_YEAR = "2025"
APP_LICENSE = "MIT"

SERVER_PORT = 8000

DAYS = [("Пн", "mon"), ("Вт", "tue"), ("Ср", "wed"), ("Чт", "thu"),
        ("Пт", "fri"), ("Сб", "sat"), ("Вс", "sun")]


# ======================================================================
# Нативный диалог выбора цвета
# ======================================================================
class _CHOOSECOLORW(ctypes.Structure):
    _fields_ = [
        ("lStructSize",    wintypes.DWORD),
        ("hwndOwner",      wintypes.HWND),
        ("hInstance",      wintypes.HWND),
        ("rgbResult",      wintypes.COLORREF),
        ("lpCustColors",   ctypes.POINTER(wintypes.COLORREF)),
        ("Flags",          wintypes.DWORD),
        ("lCustData",      wintypes.LPARAM),
        ("lpfnHook",       wintypes.LPARAM),
        ("lpTemplateName", wintypes.LPCWSTR),
    ]


def _win_choose_color(parent, initial: QColor):
    CC_RGBINIT    = 0x00000001
    CC_FULLOPEN   = 0x00000002
    CC_SOLIDCOLOR = 0x00000080

    custom = (wintypes.COLORREF * 16)()
    for i in range(16):
        custom[i] = 0x00FFFFFF
    rgb = (initial.blue() << 16) | (initial.green() << 8) | initial.red()

    cc = _CHOOSECOLORW()
    ctypes.memset(ctypes.byref(cc), 0, ctypes.sizeof(cc))
    cc.lStructSize = ctypes.sizeof(cc)
    cc.hwndOwner = int(parent.winId()) if parent else 0
    cc.rgbResult = rgb
    cc.lpCustColors = custom
    cc.Flags = CC_RGBINIT | CC_FULLOPEN | CC_SOLIDCOLOR

    comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
    comdlg32.ChooseColorW.argtypes = [ctypes.POINTER(_CHOOSECOLORW)]
    comdlg32.ChooseColorW.restype = wintypes.BOOL

    if comdlg32.ChooseColorW(ctypes.byref(cc)):
        r = cc.rgbResult & 0xFF
        g = (cc.rgbResult >> 8) & 0xFF
        b = (cc.rgbResult >> 16) & 0xFF
        return QColor(r, g, b, 255)
    return None


def native_color_dialog(parent, initial: QColor, title: str):
    if os.name == "nt":
        try:
            return _win_choose_color(parent, initial)
        except Exception as e:
            print(f"[ColorDialog] Нативный диалог недоступен: {e}")
    color = QColorDialog.getColor(initial, parent, title)
    return color if color.isValid() else None


# ======================================================================
# Главное окно
# ======================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(1500, 950)

        icon_path = Path(__file__).parent / "assets" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.ffmpeg_manager = FFmpegManager()
        self.ffmpeg_path = self._prepare_ffmpeg()

        self.playlist_manager = PlaylistManager()
        self.stream_engine = StreamEngine(ffmpeg_path=self.ffmpeg_path)
        self.scheduler = OverlayScheduler(self.playlist_manager, self.stream_engine)

        self.overlays: list[Overlay] = []
        self.server_thread = None

        self._font_color = QColor(255, 255, 255)
        self._font_alpha = 255
        self._box_color = QColor(0, 0, 0)
        self._box_alpha = 153

        # IP-настройки
        self.local_ip = self._pick_default_ip()
        self.base_url = f"http://{self.local_ip}:{SERVER_PORT}"

        self._init_ui()
        self._create_menubar()
        self._connect_signals()
        self._update_ffmpeg_status()
        self._refresh_ip_combo()
        self._update_playlist_url()
        self._refresh_color_buttons()

    # ------------------------------------------------------------------
    # Сетевые интерфейсы
    # ------------------------------------------------------------------
    def _get_available_ips(self) -> list[tuple[str, str]]:
        """
        Возвращает [(имя_интерфейса, ip), ...] только для «живых» IPv4-адресов.
        Первый пункт — localhost (127.0.0.1) — всегда доступен.
        Отбрасываются: APIPA (169.254.x.x), multicast, broadcast,
        unspecified (0.0.0.0), reserved, а также выключенные интерфейсы.
        """
        result = [("Локальный (только этот ПК)", "127.0.0.1")]

        def is_usable(ip_str: str) -> bool:
            """True, если по этому адресу можно достучаться по сети."""
            try:
                ip = ipaddress.IPv4Address(ip_str)
            except ValueError:
                return False
            # Служебные и мёртвые диапазоны
            if ip.is_loopback:        # 127.x.x.x — уже добавлен отдельно
                return False
            if ip.is_link_local:      # 169.254.x.x — APIPA, DHCP не сработал
                return False
            if ip.is_multicast:       # 224–239.x.x.x — групповые рассылки
                return False
            if ip.is_unspecified:     # 0.0.0.0
                return False
            if ip.is_reserved:        # 240.x.x.x и прочие зарезервированные
                return False
            return True

        try:
            import psutil
            if_stats = psutil.net_if_stats()  # состояние up/down

            for iface, addrs in psutil.net_if_addrs().items():
                # Пропускаем выключенные интерфейсы (кабель выдернут, Wi-Fi off)
                if iface in if_stats and not if_stats[iface].isup:
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET and is_usable(addr.address):
                        result.append((iface, addr.address))

            # Сортировка: Ethernet → Wi-Fi → остальные
            def sort_key(item: tuple[str, str]) -> tuple[int, str]:
                name = item[0].lower()
                if "ethernet" in name:
                    return (0, name)
                if "wi-fi" in name or "wifi" in name or "wlan" in name:
                    return (1, name)
                return (2, name)

            # Первый элемент (localhost) оставляем на месте, сортируем только остальное
            if len(result) > 1:
                tail = sorted(result[1:], key=sort_key)
                result = [result[0]] + tail

            return result

        except ImportError:
            # Fallback без psutil — фильтруем по диапазонам IP
            try:
                hostname = socket.gethostname()
                _, _, ips = socket.gethostbyname_ex(hostname)
                ips = [ip for ip in ips if is_usable(ip)]
                result.extend(
                    (f"Интерфейс {i + 1}", ip) for i, ip in enumerate(ips)
                )
                return result
            except Exception as e:
                print(f"[IP] Не удалось получить адреса: {e}")
                return result

    def _pick_default_ip(self) -> str:
        """
        Возвращает первый реальный (не localhost) IP.
        Если реальных нет — 127.0.0.1.
        """
        ips = self._get_available_ips()
        for _, ip in ips:
            if not ip.startswith("127."):
                return ip
        return "127.0.0.1"

    def _is_local_ip(self, ip: str) -> bool:
        if ip == "127.0.0.1":
            return True
        return any(local_ip == ip for _, local_ip in self._get_available_ips())

    def _refresh_ip_combo(self):
        """Пересобирает список IP-адресов, сохраняя текущий выбор."""
        current = self.local_ip
        self.combo_ip.blockSignals(True)
        self.combo_ip.clear()

        ips = self._get_available_ips()
        for iface, ip in ips:
            self.combo_ip.addItem(f"{iface} — {ip}", userData=ip)

        self.combo_ip.addItem("Ввести IP вручную…", userData="__custom__")

        # Выбираем текущий IP, если он есть в списке
        idx = self.combo_ip.findData(current)
        if idx >= 0:
            self.combo_ip.setCurrentIndex(idx)
        else:
            # Если текущий IP не обнаружен (например, ручной ввод) — добавляем первым
            self.combo_ip.insertItem(0, f"Текущий — {current}", userData=current)
            self.combo_ip.setCurrentIndex(0)

        self.combo_ip.blockSignals(False)

    def _on_ip_combo_changed(self):
        data = self.combo_ip.currentData()
        if data == "__custom__":
            text, ok = QInputDialog.getText(
                self, "Свой IP-адрес",
                "Введите IP или домен для ссылок исходящего плейлиста:",
                text=self.local_ip,
            )
            if ok and text.strip():
                self._set_ip(text.strip())
            else:
                # Откат: пересобираем combo по текущему значению
                self._refresh_ip_combo()
        elif data:
            self._set_ip(data)

    def _set_ip(self, ip: str):
        old = self.local_ip
        self.local_ip = ip
        self.base_url = f"http://{self.local_ip}:{SERVER_PORT}"
        self._update_playlist_url()

        is_loopback = ip.startswith("127.")
        if not self._is_local_ip(ip) and not is_loopback:
            QMessageBox.warning(
                self, "Внимание",
                f"IP {ip} не обнаружен ни на одном сетевом интерфейсе.\n\n"
                "Это нормально, если вы используете внешний IP через NAT или VPN — "
                "тогда убедитесь, что порт 8000 проброшен на эту машину.\n\n"
                "В остальных случаях плейлист по этому адресу будет недоступен из локальной сети."
            )

        if old != ip:
            if is_loopback:
                self.label_status.setText(
                    f"IP: 127.0.0.1 — плейлист доступен только на этом ПК"
                )
            else:
                self.label_status.setText(
                    f"IP исходящего плейлиста изменён: {old} → {ip}"
                )

    # ------------------------------------------------------------------
    def _prepare_ffmpeg(self) -> str:
        try:
            return self.ffmpeg_manager.ensure_ffmpeg()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка FFmpeg",
                                 f"Не удалось найти или скачать FFmpeg:\n{e}")
            return "ffmpeg"

    def _update_ffmpeg_status(self):
        if self.stream_engine.check_ffmpeg():
            self.label_ffmpeg.setText(f"✅ FFmpeg: {self.ffmpeg_path}")
            self.label_ffmpeg.setStyleSheet("color: green;")
        else:
            self.label_ffmpeg.setText(f"❌ FFmpeg не найден: {self.ffmpeg_path}")
            self.label_ffmpeg.setStyleSheet("color: red;")

    def _update_playlist_url(self):
        self.edit_playlist_url.setText(f"{self.base_url}/playlist.m3u")

    # ------------------------------------------------------------------
    # Меню
    # ------------------------------------------------------------------
    def _create_menubar(self):
        menubar = self.menuBar()

        help_menu = menubar.addMenu("Справка")
        act_help = QAction("Руководство пользователя", self)
        act_help.setShortcut("F1")
        act_help.triggered.connect(self.show_help)
        help_menu.addAction(act_help)
        help_menu.addSeparator()
        act_about = QAction("О программе", self)
        act_about.triggered.connect(self.show_about)
        help_menu.addAction(act_about)

        file_menu = menubar.addMenu("Файл")
        act_exit = QAction("Выход", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

    def show_help(self):
        readme_path = Path(__file__).parent / "README.md"
        text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else \
            "# Руководство пользователя\n\nФайл README.md не найден."

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{APP_NAME} — Руководство пользователя")
        dlg.resize(900, 700)
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setMarkdown(text)
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
        dlg.exec()

    def show_about(self):
        html = f"""
        <h2>{APP_NAME}</h2>
        <p><b>Версия:</b> {APP_VERSION}</p>
        <p>Программа для организации IPTV-трансляции с наложением
        бегущих строк на выбранные каналы по расписанию.</p>
        <hr>
        <p><b>Автор:</b> {APP_AUTHOR}</p>
        <p><b>© {APP_YEAR} {APP_AUTHOR}. Все права защищены.</b></p>
        <p><b>Лицензия:</b> {APP_LICENSE}</p>
        <hr>
        <p><b>Технологии:</b></p>
        <ul>
            <li>Python 3.10+</li>
            <li>PyQt6</li>
            <li>FastAPI + Uvicorn</li>
            <li>FFmpeg</li>
            <li>APScheduler</li>
            <li>psutil</li>
        </ul>
        <p style="color: #666; font-size: 10px;">
        Программа предоставляется «как есть», без каких-либо гарантий.
        </p>
        """
        QMessageBox.about(self, "О программе", html)

    # ------------------------------------------------------------------
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # ЛЕВАЯ КОЛОНКА
        left = QWidget()
        left.setMinimumWidth(360)
        left.setMaximumWidth(440)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.label_ffmpeg = QLabel("Проверка FFmpeg...")
        left_layout.addWidget(self.label_ffmpeg)

        self.btn_load = QPushButton("Загрузить M3U")
        left_layout.addWidget(self.btn_load)

        srv_layout = QHBoxLayout()
        self.btn_start_server = QPushButton("Запустить сервер")
        self.btn_stop_server = QPushButton("Остановить сервер")
        self.btn_stop_server.setEnabled(False)
        srv_layout.addWidget(self.btn_start_server)
        srv_layout.addWidget(self.btn_stop_server)
        left_layout.addLayout(srv_layout)

        # Исходящий плейлист
        outgoing = QGroupBox("Исходящий плейлист (для клиента)")
        out_l = QVBoxLayout(outgoing)

        # --- Выбор сетевого интерфейса ---
        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("Интерфейс:"))
        self.combo_ip = QComboBox()
        self.combo_ip.setMinimumWidth(200)
        ip_row.addWidget(self.combo_ip, 1)
        self.btn_refresh_ips = QPushButton("⟳")
        self.btn_refresh_ips.setMaximumWidth(32)
        self.btn_refresh_ips.setToolTip("Обновить список IP-адресов")
        ip_row.addWidget(self.btn_refresh_ips)
        out_l.addLayout(ip_row)

        # URL
        self.edit_playlist_url = QLineEdit()
        self.edit_playlist_url.setReadOnly(True)
        self.edit_playlist_url.setStyleSheet("background-color: #f0f0f0;")
        out_l.addWidget(self.edit_playlist_url)

        row = QHBoxLayout()
        self.btn_copy_url = QPushButton("Копировать")
        self.btn_save_playlist = QPushButton("Сохранить…")
        row.addWidget(self.btn_copy_url)
        row.addWidget(self.btn_save_playlist)
        out_l.addLayout(row)

        self.btn_show_playlist = QPushButton("Показать содержимое")
        out_l.addWidget(self.btn_show_playlist)

        hint = QLabel(
            "Выберите интерфейс, по которому клиенты будут подключаться. "
            "Смена IP не требует перезапуска сервера."
        )
        hint.setStyleSheet("color: #555; font-size: 11px;")
        hint.setWordWrap(True)
        out_l.addWidget(hint)

        left_layout.addWidget(outgoing)

        scheduled = QGroupBox("Запланированные оверлеи")
        sch_l = QVBoxLayout(scheduled)
        self.list_overlays = QListWidget()
        sch_l.addWidget(self.list_overlays)
        self.btn_remove_overlay = QPushButton("Удалить выбранный оверлей")
        sch_l.addWidget(self.btn_remove_overlay)
        left_layout.addWidget(scheduled, 1)

        self.label_status = QLabel("Сервер не запущен")
        left_layout.addWidget(self.label_status)
        main_layout.addWidget(left)

        # ПАНЕЛЬ 1
        incoming_group = QGroupBox("1. Входящий плейлист")
        incoming_group.setMinimumWidth(280)
        inc_l = QVBoxLayout(incoming_group)
        inc_l.addWidget(QLabel("Отметьте каналы, которые пойдут в трансляцию\n"
                               "(они попадут в исходящий плейлист):"))
        self.list_incoming = QListWidget()
        self.list_incoming.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        inc_l.addWidget(self.list_incoming, 1)
        row_inc = QHBoxLayout()
        self.btn_incoming_all = QPushButton("Выбрать всё")
        self.btn_incoming_clear = QPushButton("Снять всё")
        self.btn_incoming_remove = QPushButton("Удалить")
        row_inc.addWidget(self.btn_incoming_all)
        row_inc.addWidget(self.btn_incoming_clear)
        row_inc.addWidget(self.btn_incoming_remove)
        inc_l.addLayout(row_inc)

        # ПАНЕЛЬ 2
        broadcast_group = QGroupBox("2. Трансляция (каналы с оверлеем)")
        broadcast_group.setMinimumWidth(280)
        bro_l = QVBoxLayout(broadcast_group)
        bro_l.addWidget(QLabel("Отметьте каналы, на которые наложить строку:"))
        self.list_broadcast = QListWidget()
        self.list_broadcast.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        bro_l.addWidget(self.list_broadcast, 1)
        row_bro = QHBoxLayout()
        self.btn_broadcast_all = QPushButton("Выбрать всё")
        self.btn_broadcast_clear = QPushButton("Снять всё")
        row_bro.addWidget(self.btn_broadcast_all)
        row_bro.addWidget(self.btn_broadcast_clear)
        bro_l.addLayout(row_bro)

        # ПАНЕЛЬ 3
        overlay_group = QGroupBox("3. Настройки бегущей строки")
        overlay_group.setMinimumWidth(430)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        ov_l = QVBoxLayout(scroll_content)

        ov_l.addWidget(QLabel("Текст:"))
        self.edit_text = QLineEdit()
        ov_l.addWidget(self.edit_text)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Шрифт:"))
        self.edit_font_path = QLineEdit()
        self.edit_font_path.setReadOnly(True)
        self.edit_font_path.setPlaceholderText("системный по умолчанию")
        font_row.addWidget(self.edit_font_path, 1)
        self.btn_choose_font = QPushButton("…")
        self.btn_choose_font.setMaximumWidth(32)
        font_row.addWidget(self.btn_choose_font)
        ov_l.addLayout(font_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Размер шрифта:"))
        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(8, 200)
        self.spin_font_size.setValue(36)
        size_row.addWidget(self.spin_font_size)
        size_row.addStretch()
        ov_l.addLayout(size_row)

        ov_l.addWidget(QLabel("Цвет текста:"))
        fc_row = QHBoxLayout()
        self.btn_font_color = QPushButton("Выбрать цвет…")
        fc_row.addWidget(self.btn_font_color, 1)
        fc_row.addWidget(QLabel("α:"))
        self.spin_font_alpha = QSpinBox()
        self.spin_font_alpha.setRange(0, 255)
        self.spin_font_alpha.setValue(255)
        self.spin_font_alpha.setMaximumWidth(70)
        fc_row.addWidget(self.spin_font_alpha)
        ov_l.addLayout(fc_row)

        bg_row = QHBoxLayout()
        self.chk_box = QCheckBox("Фон")
        self.chk_box.setChecked(True)
        bg_row.addWidget(self.chk_box)
        self.btn_box_color = QPushButton("Цвет фона…")
        bg_row.addWidget(self.btn_box_color, 1)
        bg_row.addWidget(QLabel("α:"))
        self.spin_box_alpha = QSpinBox()
        self.spin_box_alpha.setRange(0, 255)
        self.spin_box_alpha.setValue(153)
        self.spin_box_alpha.setMaximumWidth(70)
        bg_row.addWidget(self.spin_box_alpha)
        ov_l.addLayout(bg_row)

        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Раз в (мин):"))
        self.spin_period = QSpinBox()
        self.spin_period.setRange(1, 1440)
        self.spin_period.setValue(20)
        run_row.addWidget(self.spin_period)
        run_row.addWidget(QLabel("Скорость (px/s):"))
        self.spin_speed = QSpinBox()
        self.spin_speed.setRange(10, 1000)
        self.spin_speed.setValue(100)
        run_row.addWidget(self.spin_speed)
        ov_l.addLayout(run_row)

        ov_l.addWidget(QLabel("Расписание:"))
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("С даты:"))
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setDate(QDate.currentDate())
        date_row.addWidget(self.date_start, 1)
        ov_l.addLayout(date_row)

        end_row = QHBoxLayout()
        self.chk_end_date = QCheckBox("По дату:")
        end_row.addWidget(self.chk_end_date)
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDisplayFormat("yyyy-MM-dd")
        self.date_end.setDate(QDate.currentDate().addMonths(1))
        self.date_end.setEnabled(False)
        end_row.addWidget(self.date_end, 1)
        ov_l.addLayout(end_row)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("Старт:"))
        self.time_start = QTimeEdit()
        self.time_start.setDisplayFormat("HH:mm")
        self.time_start.setTime(QTime(9, 0))
        time_row.addWidget(self.time_start)
        time_row.addWidget(QLabel("Стоп:"))
        self.time_stop = QTimeEdit()
        self.time_stop.setDisplayFormat("HH:mm")
        self.time_stop.setTime(QTime(10, 0))
        time_row.addWidget(self.time_stop)
        ov_l.addLayout(time_row)

        ov_l.addWidget(QLabel("Дни недели:"))
        days_grid = QGridLayout()
        self.day_checkboxes = []
        for i, (label, _) in enumerate(DAYS):
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.day_checkboxes.append(cb)
            days_grid.addWidget(cb, 0, i)
        ov_l.addLayout(days_grid)

        self.btn_add_overlay = QPushButton("Добавить оверлей в расписание")
        ov_l.addWidget(self.btn_add_overlay)
        ov_l.addStretch()

        scroll.setWidget(scroll_content)
        ov_group_l = QVBoxLayout(overlay_group)
        ov_group_l.addWidget(scroll)

        main_layout.addWidget(incoming_group, 1)
        main_layout.addWidget(broadcast_group, 1)
        main_layout.addWidget(overlay_group, 2)

    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.btn_load.clicked.connect(self.load_playlist)
        self.btn_start_server.clicked.connect(self.start_server)
        self.btn_stop_server.clicked.connect(self.stop_server)
        self.btn_copy_url.clicked.connect(self.copy_playlist_url)
        self.btn_save_playlist.clicked.connect(self.save_playlist_to_file)
        self.btn_show_playlist.clicked.connect(self.show_playlist_content)
        self.btn_remove_overlay.clicked.connect(self.remove_scheduled_overlay)

        self.combo_ip.currentIndexChanged.connect(self._on_ip_combo_changed)
        self.btn_refresh_ips.clicked.connect(self._on_refresh_ips)

        self.list_incoming.itemChanged.connect(self._on_incoming_changed)
        self.btn_incoming_all.clicked.connect(self._incoming_check_all)
        self.btn_incoming_clear.clicked.connect(self._incoming_clear_all)
        self.btn_incoming_remove.clicked.connect(self.remove_incoming_channel)

        self.btn_broadcast_all.clicked.connect(self._broadcast_check_all)
        self.btn_broadcast_clear.clicked.connect(self._broadcast_clear_all)

        self.btn_choose_font.clicked.connect(self.choose_font)
        self.btn_font_color.clicked.connect(self.choose_font_color)
        self.btn_box_color.clicked.connect(self.choose_box_color)
        self.chk_end_date.toggled.connect(self.date_end.setEnabled)
        self.spin_font_alpha.valueChanged.connect(self._on_font_alpha_changed)
        self.spin_box_alpha.valueChanged.connect(self._on_box_alpha_changed)
        self.btn_add_overlay.clicked.connect(self.add_overlay)

    def _on_refresh_ips(self):
        self._refresh_ip_combo()
        self.label_status.setText(
            f"Список IP обновлён (найдено {self.combo_ip.count() - 1})"
        )

    # ------------------------------------------------------------------
    # Синхронизация панелей 1 ↔ 2 ↔ PlaylistManager.broadcast_channel_ids
    # ------------------------------------------------------------------
    def refresh_incoming_list(self):
        self.list_incoming.blockSignals(True)
        self.list_incoming.clear()
        for ch in self.playlist_manager.channels:
            item = QListWidgetItem(f"{ch.id}. {ch.name}")
            item.setData(Qt.ItemDataRole.UserRole, ch.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list_incoming.addItem(item)
        self.list_incoming.blockSignals(False)
        self._sync_broadcast_list()

    def _on_incoming_changed(self, _item):
        self._sync_broadcast_list()

    def _sync_broadcast_list(self):
        checked = set()
        for i in range(self.list_broadcast.count()):
            it = self.list_broadcast.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                checked.add(it.data(Qt.ItemDataRole.UserRole))

        self.list_broadcast.blockSignals(True)
        self.list_broadcast.clear()
        for i in range(self.list_incoming.count()):
            it = self.list_incoming.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                cid = it.data(Qt.ItemDataRole.UserRole)
                new_it = QListWidgetItem(it.text())
                new_it.setData(Qt.ItemDataRole.UserRole, cid)
                new_it.setFlags(new_it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                new_it.setCheckState(
                    Qt.CheckState.Checked if cid in checked else Qt.CheckState.Unchecked
                )
                self.list_broadcast.addItem(new_it)
        self.list_broadcast.blockSignals(False)

        broadcast_ids = set()
        for i in range(self.list_broadcast.count()):
            cid = self.list_broadcast.item(i).data(Qt.ItemDataRole.UserRole)
            broadcast_ids.add(cid)
        self.playlist_manager.broadcast_channel_ids = broadcast_ids

    def _incoming_check_all(self):
        self.list_incoming.blockSignals(True)
        for i in range(self.list_incoming.count()):
            self.list_incoming.item(i).setCheckState(Qt.CheckState.Checked)
        self.list_incoming.blockSignals(False)
        self._sync_broadcast_list()

    def _incoming_clear_all(self):
        self.list_incoming.blockSignals(True)
        for i in range(self.list_incoming.count()):
            self.list_incoming.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.list_incoming.blockSignals(False)
        self._sync_broadcast_list()

    def _broadcast_check_all(self):
        for i in range(self.list_broadcast.count()):
            self.list_broadcast.item(i).setCheckState(Qt.CheckState.Checked)

    def _broadcast_clear_all(self):
        for i in range(self.list_broadcast.count()):
            self.list_broadcast.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _get_broadcast_channel_ids(self) -> list[int]:
        ids = []
        for i in range(self.list_broadcast.count()):
            it = self.list_broadcast.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                ids.append(it.data(Qt.ItemDataRole.UserRole))
        return ids

    def remove_incoming_channel(self):
        row = self.list_incoming.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Внимание", "Выберите канал")
            return
        item = self.list_incoming.item(row)
        cid = item.data(Qt.ItemDataRole.UserRole)
        if self.stream_engine.is_active(cid):
            self.stream_engine.stop_overlay(cid)
        self.playlist_manager.remove_channel(row)
        self.refresh_incoming_list()

    # ------------------------------------------------------------------
    def _refresh_color_buttons(self):
        self._apply_color_button_style(
            self.btn_font_color, self._font_color, self._font_alpha, "Текст"
        )
        self._apply_color_button_style(
            self.btn_box_color, self._box_color, self._box_alpha, "Фон"
        )

    def _apply_color_button_style(self, button: QPushButton,
                                  color: QColor, alpha: int, label: str):
        r, g, b = color.red(), color.green(), color.blue()
        text_color = "black" if color.lightness() > 128 else "white"
        button.setStyleSheet(
            f"background-color: rgba({r},{g},{b},{alpha}); color: {text_color};"
        )
        button.setText(f"{label}: #{r:02X}{g:02X}{b:02X}  α={alpha}")

    def choose_font_color(self):
        color = native_color_dialog(self, self._font_color, "Цвет текста")
        if color is not None:
            self._font_color = color
            self._refresh_color_buttons()

    def choose_box_color(self):
        color = native_color_dialog(self, self._box_color, "Цвет фона")
        if color is not None:
            self._box_color = color
            self._refresh_color_buttons()

    def _on_font_alpha_changed(self, v):
        self._font_alpha = v
        self._refresh_color_buttons()

    def _on_box_alpha_changed(self, v):
        self._box_alpha = v
        self._refresh_color_buttons()

    def _qcolor_to_ffmpeg_hex(self, color: QColor) -> str:
        return f"0x{color.red():02X}{color.green():02X}{color.blue():02X}"

    def choose_font(self):
        start = "C:/Windows/Fonts" if os.name == "nt" else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать шрифт", start,
            "Шрифты (*.ttf *.otf);;Все файлы (*.*)"
        )
        if path:
            self.edit_font_path.setText(path)

    # ------------------------------------------------------------------
    def copy_playlist_url(self):
        QApplication.clipboard().setText(self.edit_playlist_url.text())
        self.label_status.setText("URL скопирован")

    def _generate_playlist_text(self) -> str:
        if not self.playlist_manager.channels:
            raise RuntimeError("Плейлист пуст — загрузите M3U.")
        if not self.playlist_manager.broadcast_channel_ids:
            raise RuntimeError(
                "Не выбрано ни одного канала для трансляции.\n"
                "Отметьте каналы галочками в панели «1. Входящий плейлист»."
            )
        return self.playlist_manager.generate_server_m3u(base_url=self.base_url)

    def save_playlist_to_file(self):
        try:
            content = self._generate_playlist_text()
        except Exception as e:
            QMessageBox.warning(self, "Внимание", str(e))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить плейлист", "outgoing_playlist.m3u", "M3U (*.m3u)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(self, "Успех",
                                    "Плейлист сохранён.\nСсылки работают, пока сервер запущен.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def show_playlist_content(self):
        try:
            content = self._generate_playlist_text()
        except Exception as e:
            QMessageBox.warning(self, "Внимание", str(e))
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Плейлист")
        dlg.setText(f"Содержимое (каналов: "
                    f"{len(self.playlist_manager.broadcast_channel_ids)}):")
        dlg.setDetailedText(content)
        dlg.exec()

    # ------------------------------------------------------------------
    def load_playlist(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть M3U", "", "M3U (*.m3u *.m3u8);;Все файлы (*.*)"
        )
        if not path:
            return
        try:
            count = self.playlist_manager.load_from_file(path)
            self.refresh_incoming_list()
            if count == 0:
                QMessageBox.warning(self, "Плейлист пуст", "Каналов не найдено.")
            else:
                QMessageBox.information(self, "Успех", f"Загружено каналов: {count}")
        except Exception as e:
            tb = traceback.format_exc()
            print(tb)
            QMessageBox.critical(self, "Ошибка", f"{type(e).__name__}: {e}\n\n{tb}")

    # ------------------------------------------------------------------
    def _get_days_of_week(self) -> str:
        selected = [cron for cb, (_, cron) in zip(self.day_checkboxes, DAYS)
                    if cb.isChecked()]
        if not selected or len(selected) == 7:
            return "*"
        return ",".join(selected)

    def add_overlay(self):
        channel_ids = self._get_broadcast_channel_ids()
        if not channel_ids:
            QMessageBox.warning(self, "Внимание",
                                "Отметьте каналы в панели «2. Трансляция»")
            return
        text = self.edit_text.text().strip()
        if not text:
            QMessageBox.warning(self, "Внимание", "Введите текст")
            return

        overlay_id = f"ov_{len(self.overlays) + 1}"
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        end_date = ""
        if self.chk_end_date.isChecked():
            end_date = self.date_end.date().toString("yyyy-MM-dd")

        overlay = Overlay(
            id=overlay_id,
            channel_ids=channel_ids,
            text=text,
            start_date=start_date,
            end_date=end_date,
            start_time=self.time_start.time().toString("HH:mm"),
            stop_time=self.time_stop.time().toString("HH:mm"),
            days_of_week=self._get_days_of_week(),
            speed=self.spin_speed.value(),
            repeat_period_sec=self.spin_period.value() * 60,
            font_path=self.edit_font_path.text().strip(),
            font_size=self.spin_font_size.value(),
            font_color=self._qcolor_to_ffmpeg_hex(self._font_color),
            font_alpha=self._font_alpha,
            box_enabled=self.chk_box.isChecked(),
            box_color=self._qcolor_to_ffmpeg_hex(self._box_color),
            box_alpha=self._box_alpha,
        )

        approx_width = 1920 + len(text) * overlay.font_size * 0.6
        if overlay.speed * overlay.repeat_period_sec < approx_width:
            QMessageBox.warning(
                self, "Внимание",
                f"Строка может не успеть пробежать за {overlay.repeat_period_sec // 60} мин."
            )
            return

        try:
            self.scheduler.add_schedule(overlay)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.overlays.append(overlay)

        ch_str = ", ".join(str(c) for c in channel_ids)
        days_label = "ежедневно" if overlay.days_of_week == "*" else overlay.days_of_week
        period_label = f"раз в {overlay.repeat_period_sec // 60} мин"
        date_label = f"с {start_date}"
        if end_date:
            date_label += f" по {end_date}"
        font_label = Path(overlay.font_path).name if overlay.font_path else "системный"

        self.list_overlays.addItem(
            f"[{overlay.id}] Каналы [{ch_str}]: '{text}'\n"
            f"    {date_label}, {overlay.start_time}–{overlay.stop_time}, {days_label}\n"
            f"    {period_label}, {overlay.speed}px/s, {overlay.font_size}px, {font_label}"
        )
        QMessageBox.information(self, "Успех",
                                f"Оверлей добавлен на {len(channel_ids)} канал(ов).")

    def remove_scheduled_overlay(self):
        row = self.list_overlays.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Внимание", "Выберите оверлей")
            return
        overlay = self.overlays[row]
        for cid in overlay.channel_ids:
            if self.stream_engine.is_active(cid):
                self.stream_engine.stop_overlay(cid)
        self.scheduler.remove_schedule(overlay.id)
        del self.overlays[row]
        self.list_overlays.takeItem(row)

    # ------------------------------------------------------------------
    def start_server(self):
        if not self.stream_engine.check_ffmpeg():
            QMessageBox.critical(self, "FFmpeg не найден", "Сервер не запущен.")
            return
        if self.server_thread and self.server_thread.is_alive():
            QMessageBox.information(self, "Информация", "Сервер уже запущен")
            return

        # ВАЖНО: передаём функцию, а не строку — URL будет актуальным
        # даже если пользователь сменит IP после запуска сервера.
        app = create_app(
            self.playlist_manager,
            self.stream_engine,
            lambda: self.base_url,
        )

        def run():
            try:
                uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT, log_level="info")
            except Exception as e:
                print(f"[Server] Ошибка: {e}")

        self.server_thread = threading.Thread(target=run, daemon=True)
        self.server_thread.start()
        self.btn_start_server.setEnabled(False)
        self.btn_stop_server.setEnabled(True)
        self.label_status.setText(f"Сервер запущен: {self.base_url}/playlist.m3u")

    def stop_server(self):
        self.stream_engine.shutdown()
        self.scheduler.shutdown()
        QMessageBox.information(self, "Информация",
                                "FFmpeg-процессы и планировщик остановлены.")
        self.btn_start_server.setEnabled(True)
        self.btn_stop_server.setEnabled(False)
        self.label_status.setText("Сервер остановлен")

    def closeEvent(self, event):
        self.stream_engine.shutdown()
        try:
            self.scheduler.shutdown()
        except Exception:
            pass
        event.accept()
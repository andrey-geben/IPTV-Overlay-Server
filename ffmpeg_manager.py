# ffmpeg_manager.py
import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import tarfile
import platform
from pathlib import Path


class FFmpegManager:
    """
    Управляет наличием FFmpeg: проверяет, скачивает и возвращает путь к бинарнику.
    """

    def __init__(self, install_dir: str = "ffmpeg_bin"):
        self.install_dir = Path(install_dir)
        self.ffmpeg_path = None

    def ensure_ffmpeg(self) -> str:
        """
        Главный метод. Проверяет наличие FFmpeg в системе или в локальной папке.
        Если не найден — скачивает и распаковывает.
        Возвращает путь к исполняемому файлу ffmpeg.
        """
        # 1. Проверяем, есть ли ffmpeg в системе (PATH)
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            print(f"[FFmpegManager] Найден системный FFmpeg: {system_ffmpeg}")
            self.ffmpeg_path = system_ffmpeg
            return self.ffmpeg_path

        # 2. Проверяем, не скачан ли уже FFmpeg в локальную папку
        local_ffmpeg = self._get_local_ffmpeg_path()
        if local_ffmpeg and local_ffmpeg.exists():
            print(f"[FFmpegManager] Найден локальный FFmpeg: {local_ffmpeg}")
            self.ffmpeg_path = str(local_ffmpeg)
            return self.ffmpeg_path

        # 3. Если нигде нет — скачиваем
        print("[FFmpegManager] FFmpeg не найден. Начинаю загрузку...")
        self._download_and_extract()

        # После распаковки снова проверяем локальный путь
        local_ffmpeg = self._get_local_ffmpeg_path()
        if local_ffmpeg and local_ffmpeg.exists():
            self.ffmpeg_path = str(local_ffmpeg)
            print(f"[FFmpegManager] FFmpeg успешно установлен: {self.ffmpeg_path}")
            return self.ffmpeg_path
        else:
            raise RuntimeError("Не удалось найти или скачать FFmpeg.")

    def _get_local_ffmpeg_path(self) -> Path | None:
        """Возвращает ожидаемый путь к локальному бинарнику FFmpeg."""
        exe_name = "ffmpeg.exe" if os.name == 'nt' else "ffmpeg"
        # BtbN распаковывает архив в подпапку, поэтому ищем файл рекурсивно
        # Но для простоты предполагаем, что мы распакуем его в корень install_dir
        return self.install_dir / exe_name

    def _download_and_extract(self):
        """Скачивает и распаковывает архив с FFmpeg для текущей ОС."""
        system = platform.system().lower()
        arch = platform.machine().lower()

        # Определяем URL для скачивания в зависимости от ОС и архитектуры
        # Используем BtbN/FFmpeg-Builds (статические сборки)
        if system == "windows":
            if "64" in arch or "amd64" in arch:
                # Windows x86_64
                url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
            else:
                raise RuntimeError(f"Неподдерживаемая архитектура Windows: {arch}")
        elif system == "linux":
            if "64" in arch or "x86_64" in arch:
                url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
            elif "arm" in arch or "aarch64" in arch:
                url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
            else:
                raise RuntimeError(f"Неподдерживаемая архитектура Linux: {arch}")
        elif system == "darwin":  # macOS
            # BtbN в основном предоставляет сборки для Windows и Linux.
            # Для macOS можно использовать сборки от Martin Riedl или eugeneware/ffmpeg-static.
            # В качестве запасного варианта используем универсальную сборку (если доступна).
            # Для простоты в этом примере оставим macOS без автоматической загрузки,
            # но вы можете добавить URL для macOS (например, с https://ffmpeg.martin-riedl.de/).
            print("[FFmpegManager] Для macOS автоматическая загрузка не настроена. "
                  "Пожалуйста, установите FFmpeg через 'brew install ffmpeg'.")
            raise RuntimeError("Автоматическая загрузка для macOS не поддерживается в этой конфигурации.")
        else:
            raise RuntimeError(f"Неподдерживаемая операционная система: {system}")

        # Создаем папку для установки
        self.install_dir.mkdir(parents=True, exist_ok=True)

        # Скачиваем архив
        archive_path = self.install_dir / os.path.basename(url)
        print(f"[FFmpegManager] Скачивание: {url}")
        urllib.request.urlretrieve(url, archive_path)

        # Распаковываем
        print("[FFmpegManager] Распаковка...")
        if url.endswith(".zip"):
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(self.install_dir)
        elif url.endswith(".tar.xz"):
            with tarfile.open(archive_path, 'r:xz') as tar_ref:
                tar_ref.extractall(self.install_dir)
        else:
            raise RuntimeError(f"Неизвестный формат архива: {archive_path}")

        # Удаляем скачанный архив (опционально)
        archive_path.unlink()

        # BtbN распаковывает файлы в подпапку (например, ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe)
        # Нам нужно переместить бинарник в корень install_dir или запомнить путь к нему.
        # Найдем исполняемый файл рекурсивно и переместим его.
        self._flatten_extracted_files()

    def _flatten_extracted_files(self):
        """
        Перемещает ffmpeg и ffprobe из вложенных папок в корень install_dir.
        """
        target_names = ["ffmpeg.exe", "ffmpeg", "ffprobe.exe", "ffprobe"]
        # Ищем все файлы в install_dir рекурсивно
        for file_path in self.install_dir.rglob("*"):
            if file_path.is_file() and file_path.name in target_names:
                # Перемещаем в корень install_dir
                target_path = self.install_dir / file_path.name
                if not target_path.exists():
                    shutil.move(str(file_path), str(target_path))
                    print(f"[FFmpegManager] Перемещен: {file_path.name}")

    def get_ffmpeg_path(self) -> str:
        """Возвращает путь к ffmpeg. Должен вызываться после ensure_ffmpeg()."""
        if not self.ffmpeg_path:
            raise RuntimeError("Сначала вызовите ensure_ffmpeg()")
        return self.ffmpeg_path
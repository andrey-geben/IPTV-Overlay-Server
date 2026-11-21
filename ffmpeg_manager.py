# ffmpeg_manager.py
"""Поиск/загрузка FFmpeg. Один экземпляр класса, пути рядом с exe/скриптом."""
from __future__ import annotations

import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path


class FFmpegManager:
    def __init__(self, install_dir: str = "ffmpeg_bin"):
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        self.install_dir = base / install_dir
        self.ffmpeg_path: str | None = None

    def _get_local_ffmpeg_path(self) -> Path | None:
        exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        candidates = [self.install_dir / exe_name]
        if getattr(sys, "frozen", False):
            candidates.insert(0, Path(sys.executable).parent / exe_name)
        else:
            candidates.insert(0, Path(__file__).parent / exe_name)
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / exe_name)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def ensure_ffmpeg(self) -> str:
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            print(f"[FFmpegManager] Системный FFmpeg: {system_ffmpeg}")
            self.ffmpeg_path = system_ffmpeg
            return self.ffmpeg_path

        local = self._get_local_ffmpeg_path()
        if local:
            print(f"[FFmpegManager] Локальный FFmpeg: {local}")
            self.ffmpeg_path = str(local)
            return self.ffmpeg_path

        print("[FFmpegManager] FFmpeg не найден, скачиваю...")
        self._download_and_extract()
        local = self._get_local_ffmpeg_path()
        if not local:
            raise RuntimeError("Не удалось найти или скачать FFmpeg.")
        self.ffmpeg_path = str(local)
        print(f"[FFmpegManager] FFmpeg установлен: {self.ffmpeg_path}")
        return self.ffmpeg_path

    def _download_and_extract(self) -> None:
        system = platform.system().lower()
        arch = platform.machine().lower()
        if system == "windows":
            if "64" not in arch and "amd64" not in arch:
                raise RuntimeError(f"Неподдерживаемая архитектура Windows: {arch}")
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        elif system == "linux":
            if "arm" in arch or "aarch64" in arch:
                url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
            elif "64" in arch or "x86_64" in arch:
                url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
            else:
                raise RuntimeError(f"Неподдерживаемая архитектура Linux: {arch}")
        else:
            raise RuntimeError(
                f"Автоматическая загрузка FFmpeg для {system} не поддерживается. "
                "Установите FFmpeg в PATH."
            )

        self.install_dir.mkdir(parents=True, exist_ok=True)
        archive = self.install_dir / Path(url).name
        print(f"[FFmpegManager] Скачивание: {url}")
        with urllib.request.urlopen(url, timeout=120) as response, open(archive, "wb") as out:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)

        try:
            if archive.suffix.lower() == ".zip":
                self._safe_extract_zip(archive)
            else:
                self._safe_extract_tar(archive)
        finally:
            archive.unlink(missing_ok=True)
        self._flatten_extracted_files()

    def _safe_extract_zip(self, archive: Path) -> None:
        dest = self.install_dir.resolve()
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if target != dest and dest not in target.parents:
                    raise RuntimeError(f"Небезопасный путь в архиве: {member.filename}")
            zf.extractall(dest)

    def _safe_extract_tar(self, archive: Path) -> None:
        dest = self.install_dir.resolve()
        with tarfile.open(archive, "r:*") as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:  # Python < 3.12
                for member in tf.getmembers():
                    target = (dest / member.name).resolve()
                    if target != dest and dest not in target.parents:
                        raise RuntimeError(f"Небезопасный путь в архиве: {member.name}")
                tf.extractall(dest)

    def _flatten_extracted_files(self) -> None:
        names = {"ffmpeg.exe", "ffmpeg", "ffprobe.exe", "ffprobe"}
        for path in self.install_dir.rglob("*"):
            if path.is_file() and path.name in names:
                target = self.install_dir / path.name
                if path != target and not target.exists():
                    shutil.move(str(path), str(target))
                    print(f"[FFmpegManager] Перемещён: {path.name}")

    def get_ffmpeg_path(self) -> str:
        if not self.ffmpeg_path:
            raise RuntimeError("Сначала вызовите ensure_ffmpeg()")
        return self.ffmpeg_path

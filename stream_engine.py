# stream_engine.py
"""Запуск FFmpeg для HLS-каналов с бегущей строкой."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Optional, TextIO

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class StreamEngine:
    def __init__(self, hls_root: str = "hls_output", ffmpeg_path: str = "ffmpeg"):
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        self.hls_root = (base / hls_root).resolve()
        self.hls_root.mkdir(parents=True, exist_ok=True)
        self.processes: Dict[int, subprocess.Popen] = {}
        self._log_files: Dict[int, TextIO] = {}
        self._lock = threading.RLock()
        self.ffmpeg_path = ffmpeg_path
        self._ffmpeg_ok: bool | None = None
        self.cleanup_stale()

    def cleanup_stale(self) -> None:
        """Удаляет HLS-папки прошлых запусков."""
        for child in self.hls_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    def check_ffmpeg(self) -> bool:
        if self._ffmpeg_ok is not None:
            return self._ffmpeg_ok
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, creationflags=CREATE_NO_WINDOW,
            )
            self._ffmpeg_ok = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            self._ffmpeg_ok = False
        return self._ffmpeg_ok

    def start_overlay(self, channel_id: int, input_url: str, overlay) -> None:
        if not self.check_ffmpeg():
            raise RuntimeError(f"FFmpeg не найден: {self.ffmpeg_path}")

        with self._lock:
            self.stop_overlay(channel_id)
            output_dir = self.hls_root / str(channel_id)
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            playlist_path = output_dir / "index.m3u8"

            raw_font = overlay.font_path.strip() if overlay.font_path else self._get_default_font_path()
            fontfile = self._escape_font_path(raw_font)
            text_file = output_dir / "text.txt"
            text_file.write_text(overlay.text, encoding="utf-8")
            textfile = self._escape_font_path(str(text_file))

            speed = max(10, int(overlay.speed))
            period = max(1, int(overlay.repeat_period_sec))
            x_expr = f"w-mod(t*{speed}\\,{period}*{speed})"
            fontcolor = self._fmt_color(overlay.font_color, overlay.font_alpha)

            box_part = ""
            if overlay.box_enabled:
                boxcolor = self._fmt_color(overlay.box_color, overlay.box_alpha)
                box_part = f"box=1:boxcolor={boxcolor}:boxborderw=10:"

            drawtext_filter = (
                f"drawtext=fontfile='{fontfile}':"
                f"textfile='{textfile}':"
                f"expansion=none:"
                f"fontcolor={fontcolor}:"
                f"fontsize={overlay.font_size}:"
                f"{box_part}"
                f"x={x_expr}:y=h-th-50"
            )

            cmd = [
                self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "warning",
                "-re", "-i", input_url,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", drawtext_filter,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-force_key_frames", "expr:gte(t,n_forced*2)",
                "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "128k",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
                "-hls_flags", "delete_segments+omit_endlist",
                "-hls_segment_filename", str(output_dir / "segment_%03d.ts"),
                str(playlist_path),
            ]

            log_file = open(output_dir / "ffmpeg.log", "w", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    cmd, stdout=log_file, stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW,
                )
            except FileNotFoundError:
                log_file.close()
                raise RuntimeError(f"Не удалось запустить FFmpeg: {self.ffmpeg_path}")

            self.processes[channel_id] = process
            self._log_files[channel_id] = log_file
            print(f"[StreamEngine] Канал {channel_id}: оверлей запущен "
                  f"(раз в {period}с, speed={speed}, "
                  f"шрифт={Path(raw_font).name}, size={overlay.font_size}), "
                  f"PID={process.pid}")

    def stop_overlay(self, channel_id: int) -> None:
        with self._lock:
            process = self.processes.pop(channel_id, None)
            log_file = self._log_files.pop(channel_id, None)
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                        process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                print(f"[StreamEngine] Остановлен оверлей канала {channel_id}")
            if log_file:
                try:
                    log_file.close()
                except OSError:
                    pass

    def is_active(self, channel_id: int) -> bool:
        process = self.processes.get(channel_id)
        if process is None:
            return False
        if process.poll() is None:
            return True
        with self._lock:
            self.processes.pop(channel_id, None)
            log_file = self._log_files.pop(channel_id, None)
            if log_file:
                try:
                    log_file.close()
                except OSError:
                    pass
        return False

    def get_hls_dir(self, channel_id: int) -> Optional[Path]:
        if self.is_active(channel_id):
            return self.hls_root / str(channel_id)
        return None

    def _fmt_color(self, color_hex: str, alpha: int) -> str:
        if alpha >= 255:
            return color_hex
        return f"{color_hex}@{alpha / 255.0:.2f}"

    def _get_default_font_path(self) -> str:
        if os.name == "nt":
            for path in ("C:/Windows/Fonts/arial.ttf",
                         "C:/Windows/Fonts/segoeui.ttf",
                         "C:/Windows/Fonts/tahoma.ttf"):
                if os.path.exists(path):
                    return path
            return "C:/Windows/Fonts/arial.ttf"
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     "/System/Library/Fonts/Supplemental/Arial.ttf"):
            if os.path.exists(path):
                return path
        return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def _escape_font_path(self, path: str) -> str:
        return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    def shutdown(self) -> None:
        with self._lock:
            for channel_id in list(self.processes.keys()):
                self.stop_overlay(channel_id)

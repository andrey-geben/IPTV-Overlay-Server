# stream_engine.py
import subprocess
import os
import shutil
import threading
from pathlib import Path
from typing import Dict, Optional


class StreamEngine:
    def __init__(self, hls_root: str = "hls_output", ffmpeg_path: str = "ffmpeg"):
        self.hls_root = Path(hls_root)
        self.hls_root.mkdir(exist_ok=True)
        self.processes: Dict[int, subprocess.Popen] = {}
        # ВАЖНО: RLock, а не Lock — иначе start_overlay вызывает stop_overlay
        # внутри уже захваченной блокировки и получает deadlock.
        self._lock = threading.RLock()
        self.ffmpeg_path = ffmpeg_path

    def check_ffmpeg(self) -> bool:
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def start_overlay(self, channel_id: int, input_url: str, overlay):
        if not self.check_ffmpeg():
            raise RuntimeError(f"FFmpeg не найден: {self.ffmpeg_path}")

        with self._lock:
            # RLock позволяет повторно войти в stop_overlay без deadlock
            self.stop_overlay(channel_id)

            output_dir = self.hls_root / str(channel_id)
            if output_dir.exists():
                shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True)
            playlist_path = output_dir / "index.m3u8"

            raw_font = overlay.font_path.strip() if overlay.font_path else self._get_default_font_path()
            fontfile = self._escape_font_path(raw_font)
            safe_text = overlay.text.replace("'", r"\'").replace(":", r"\:")

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
                f"text='{safe_text}':"
                f"fontcolor={fontcolor}:"
                f"fontsize={overlay.font_size}:"
                f"{box_part}"
                f"x={x_expr}:y=h-th-50"
            )

            cmd = [
                self.ffmpeg_path, "-hide_banner", "-loglevel", "warning",
                "-re", "-i", input_url,
                "-vf", drawtext_filter,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-c:a", "aac", "-b:a", "128k",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
                "-hls_flags", "delete_segments",
                "-hls_segment_filename", str(output_dir / "segment_%03d.ts"),
                str(playlist_path),
            ]

            log_file = open(output_dir / "ffmpeg.log", "w", encoding="utf-8")
            try:
                process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
            except FileNotFoundError:
                log_file.close()
                raise RuntimeError(f"Не удалось запустить FFmpeg: {self.ffmpeg_path}")

            self.processes[channel_id] = process
            print(f"[StreamEngine] Канал {channel_id}: оверлей запущен "
                  f"(раз в {period}с, speed={speed}, "
                  f"шрифт={Path(raw_font).name}, size={overlay.font_size}), "
                  f"PID={process.pid}")

    def stop_overlay(self, channel_id: int):
        with self._lock:
            process = self.processes.pop(channel_id, None)
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                print(f"[StreamEngine] Остановлен оверлей канала {channel_id}")

    def is_active(self, channel_id: int) -> bool:
        process = self.processes.get(channel_id)
        return process is not None and process.poll() is None

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
            for p in ("C:/Windows/Fonts/arial.ttf",
                      "C:/Windows/Fonts/segoeui.ttf",
                      "C:/Windows/Fonts/tahoma.ttf"):
                if os.path.exists(p):
                    return p
            return "C:/Windows/Fonts/arial.ttf"
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/System/Library/Fonts/Supplemental/Arial.ttf"):
            if os.path.exists(p):
                return p
        return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def _escape_font_path(self, path: str) -> str:
        return path.replace("\\", "/").replace(":", "\\:")

    def shutdown(self):
        with self._lock:
            for channel_id in list(self.processes.keys()):
                self.stop_overlay(channel_id)
# playlist_manager.py
import re
from typing import List, Optional, Set
from models import Channel


class PlaylistManager:
    def __init__(self):
        self.channels: List[Channel] = []
        # Набор id каналов, отмеченных для трансляции (панель 1 в GUI).
        # Пустой набор = ничего не транслируется, в исходящем плейлисте пусто.
        self.broadcast_channel_ids: Set[int] = set()

    # ------------------------------------------------------------------
    # Загрузка M3U
    # ------------------------------------------------------------------
    def load_from_file(self, file_path: str) -> int:
        self.channels.clear()
        self.broadcast_channel_ids.clear()

        content = None
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError as e:
                last_err = e
                continue

        if content is None:
            raise RuntimeError(f"Не удалось прочитать файл: {last_err}")

        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("Файл пуст")

        pending_info = None
        attr_re = re.compile(r'([A-Za-z0-9_\-]+)="([^"]*)"')

        for line in lines:
            if line.startswith("#EXTINF:"):
                try:
                    meta, name = line.split(",", 1)
                except ValueError:
                    meta, name = line, ""
                attrs = dict(attr_re.findall(meta))
                pending_info = {
                    "name": name.strip(),
                    "group": attrs.get("group-title", ""),
                    "tvg_id": attrs.get("tvg-id", ""),
                }
            elif line.startswith("#"):
                continue
            else:
                if pending_info is None:
                    pending_info = {"name": f"Channel {len(self.channels) + 1}",
                                    "group": "", "tvg_id": ""}
                self.channels.append(Channel(
                    id=len(self.channels) + 1,
                    name=pending_info["name"] or f"Channel {len(self.channels) + 1}",
                    url=line,
                    group=pending_info["group"],
                ))
                pending_info = None

        return len(self.channels)

    # ------------------------------------------------------------------
    # Работа с каналами
    # ------------------------------------------------------------------
    def remove_channel(self, index: int):
        if 0 <= index < len(self.channels):
            removed = self.channels[index]
            self.broadcast_channel_ids.discard(removed.id)
            del self.channels[index]
        # Переназначаем id подряд
        for new_id, ch in enumerate(self.channels, start=1):
            ch.id = new_id
        # После переназначения id старый broadcast-набор некорректен — сбрасываем
        self.broadcast_channel_ids.clear()

    def get_channel_by_id(self, channel_id: int) -> Optional[Channel]:
        for ch in self.channels:
            if ch.id == channel_id:
                return ch
        return None

    # ------------------------------------------------------------------
    # Исходящий плейлист
    # ------------------------------------------------------------------
    def generate_server_m3u(self, base_url: str = "http://127.0.0.1:8000") -> str:
        """
        Отдаёт M3U только с теми каналами, что отмечены для трансляции.
        Если ничего не отмечено — отдаёт пустой валидный плейлист.
        """
        lines = ["#EXTM3U"]
        if not self.broadcast_channel_ids:
            # Пустой плейлист — клиент увидит «нет каналов»
            return "\n".join(lines)

        for ch in self.channels:
            if ch.id not in self.broadcast_channel_ids:
                continue
            proxy_url = f"{base_url}/stream/{ch.id}/index.m3u8"
            group_attr = f' group-title="{ch.group}"' if ch.group else ""
            lines.append(f"#EXTINF:-1{group_attr},{ch.name}")
            lines.append(proxy_url)
        return "\n".join(lines)
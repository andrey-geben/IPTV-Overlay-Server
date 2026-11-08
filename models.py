# models.py
from dataclasses import dataclass, field
from typing import List


@dataclass
class Channel:
    id: int
    name: str
    url: str
    group: str = ""


@dataclass
class Overlay:
    id: str
    channel_ids: List[int]
    text: str

    # Расписание
    start_date: str = ""              # "YYYY-MM-DD", пусто = с сегодняшнего дня
    end_date: str = ""                # "YYYY-MM-DD", пусто = без ограничения
    start_time: str = "09:00"         # "HH:MM"
    stop_time: str = "10:00"          # "HH:MM"
    days_of_week: str = "*"           # cron: "*", "mon-fri", "mon,wed"

    # Прогон
    speed: int = 100                  # пикс/сек
    repeat_period_sec: int = 1200     # как часто пробегает (сек)

    # Шрифт
    font_path: str = ""               # "" = системный
    font_size: int = 36
    font_color: str = "0xFFFFFF"      # "0xRRGGBB"
    font_alpha: int = 255             # 0–255

    # Фон
    box_enabled: bool = True
    box_color: str = "0x000000"
    box_alpha: int = 153

    active: bool = False
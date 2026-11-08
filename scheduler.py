# scheduler.py
from datetime import date, datetime, timedelta
from typing import Dict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from models import Overlay
from stream_engine import StreamEngine
from playlist_manager import PlaylistManager


class OverlayScheduler:
    def __init__(self, playlist_manager: PlaylistManager, stream_engine: StreamEngine):
        self.playlist_manager = playlist_manager
        self.stream_engine = stream_engine
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        self.jobs: Dict[str, dict] = {}

    def add_schedule(self, overlay: Overlay):
        self.remove_schedule(overlay.id)

        start_h, start_m = map(int, overlay.start_time.split(':'))
        stop_h, stop_m = map(int, overlay.stop_time.split(':'))

        start_trigger = CronTrigger(
            hour=start_h, minute=start_m,
            day_of_week=overlay.days_of_week or "*",
        )
        stop_trigger = CronTrigger(
            hour=stop_h, minute=stop_m,
            day_of_week=overlay.days_of_week or "*",
        )

        def start_job():
            if not self._should_run_today(overlay):
                return
            for cid in overlay.channel_ids:
                channel = self.playlist_manager.get_channel_by_id(cid)
                if channel:
                    try:
                        self.stream_engine.start_overlay(channel.id, channel.url, overlay)
                    except Exception as e:
                        print(f"[Scheduler] Ошибка запуска канала {cid}: {e}")
            overlay.active = True
            print(f"[Scheduler] Оверлей {overlay.id} включён")

        def stop_job():
            if not overlay.active:
                return
            for cid in overlay.channel_ids:
                self.stream_engine.stop_overlay(cid)
            overlay.active = False
            print(f"[Scheduler] Оверлей {overlay.id} выключен")

        job_start = self.scheduler.add_job(
            start_job, trigger=start_trigger,
            id=f"{overlay.id}_start", replace_existing=True,
        )
        job_stop = self.scheduler.add_job(
            stop_job, trigger=stop_trigger,
            id=f"{overlay.id}_stop", replace_existing=True,
        )
        self.jobs[overlay.id] = {'start': job_start, 'stop': job_stop}

        # Если сейчас попадаем в активное окно — запускаем в фоновом потоке
        # планировщика (DateTrigger), а НЕ синхронно в GUI-потоке.
        if self._in_current_window(overlay):
            try:
                job_now = self.scheduler.add_job(
                    start_job,
                    trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=1)),
                    id=f"{overlay.id}_immediate",
                    replace_existing=True,
                )
                self.jobs[overlay.id]['immediate'] = job_now
                print(f"[Scheduler] Оверлей {overlay.id}: немедленный старт запланирован")
            except Exception as e:
                print(f"[Scheduler] Не удалось запустить оверлей сразу: {e}")

        print(f"[Scheduler] Оверлей {overlay.id}: "
              f"{overlay.start_time}–{overlay.stop_time}, "
              f"дни='{overlay.days_of_week}'")

    def remove_schedule(self, overlay_id: str):
        if overlay_id in self.jobs:
            for job in self.jobs[overlay_id].values():
                try:
                    job.remove()
                except Exception:
                    pass
            del self.jobs[overlay_id]
        # На случай, если _immediate уже сработал и выпал из self.jobs
        try:
            self.scheduler.remove_job(f"{overlay_id}_immediate")
        except Exception:
            pass

    def shutdown(self):
        try:
            self.scheduler.shutdown()
        except Exception:
            pass

    def _should_run_today(self, overlay: Overlay) -> bool:
        today = date.today().isoformat()
        if overlay.start_date and today < overlay.start_date:
            return False
        if overlay.end_date and today > overlay.end_date:
            return False
        return True

    def _day_matches(self, overlay: Overlay) -> bool:
        dow = overlay.days_of_week or "*"
        if dow == "*":
            return True
        today_abbr = date.today().strftime("%a").lower()[:3]
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        if "-" in dow:
            parts = dow.split("-")
            if len(parts) != 2:
                return True
            try:
                i1, i2 = days.index(parts[0]), days.index(parts[1])
                cur = days.index(today_abbr)
                return i1 <= cur <= i2 if i1 <= i2 else (cur >= i1 or cur <= i2)
            except ValueError:
                return True
        return today_abbr in dow.split(",")

    def _in_current_window(self, overlay: Overlay) -> bool:
        if not self._should_run_today(overlay) or not self._day_matches(overlay):
            return False
        now = datetime.now().time()
        start = datetime.strptime(overlay.start_time, "%H:%M").time()
        stop = datetime.strptime(overlay.stop_time, "%H:%M").time()
        if start <= stop:
            return start <= now <= stop
        return now >= start or now <= stop
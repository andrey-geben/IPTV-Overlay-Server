import os
import tempfile
import time
import unittest

from models import Channel, Overlay
from playlist_manager import PlaylistManager
from scheduler import OverlayScheduler
from stream_engine import StreamEngine


class PlaylistTests(unittest.TestCase):
    def test_parse_and_stable_ids(self):
        content = (
            '#EXTM3U\n'
            '#EXTINF:-1 group-title="News" tvg-id="n1",Channel One\n'
            'http://example/1\n'
            "#EXTINF:-1 group-title='Sports',Channel Two\n"
            'http://example/2\n'
        )
        fd, path = tempfile.mkstemp(suffix=".m3u")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            pm = PlaylistManager()
            self.assertEqual(pm.load_from_file(path), 2)
            self.assertEqual([c.group for c in pm.channels], ["News", "Sports"])
            pm.broadcast_channel_ids = {1, 2}
            pm.remove_channel(0)
            self.assertEqual([c.id for c in pm.channels], [2])
            self.assertEqual(pm.broadcast_channel_ids, {2})
            self.assertIsNone(pm.get_channel_by_id(1))
            self.assertEqual(pm.get_channel_by_id(2).name, "Channel Two")
        finally:
            os.unlink(path)


class FakeStream:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.active = {}

    def start_overlay(self, channel_id, url, overlay):
        self.started.append((channel_id, overlay.id))
        self.active[channel_id] = overlay.id

    def stop_overlay(self, channel_id):
        self.stopped.append(channel_id)
        self.active.pop(channel_id, None)

    def is_active(self, channel_id):
        return channel_id in self.active


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.pm = PlaylistManager()
        self.pm.channels = [Channel(1, "One", "http://example/1"),
                           Channel(2, "Two", "http://example/2")]
        self.pm._by_id = {c.id: c for c in self.pm.channels}
        self.stream = FakeStream()
        self.scheduler = OverlayScheduler(self.pm, self.stream)

    def tearDown(self):
        self.scheduler.shutdown()

    def _overlay(self, oid="x", channels=(1,)):
        return Overlay(
            id=oid, channel_ids=list(channels), text="test",
            start_time="00:00", stop_time="23:59", days_of_week="*",
        )

    def _wait_started(self, count=1, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.stream.started) >= count:
                return True
            time.sleep(0.1)
        return False

    def test_immediate_start_and_restart(self):
        overlay = self._overlay()
        self.scheduler.add_schedule(overlay)
        self.assertTrue(self._wait_started())
        self.scheduler.shutdown()
        self.assertFalse(self.scheduler.is_running)

        self.scheduler.start()
        self.assertTrue(self.scheduler.is_running)
        self.scheduler.add_schedule(overlay)
        self.assertTrue(self._wait_started(count=2))

    def test_second_overlay_does_not_replace_first(self):
        first = self._overlay("one", (1,))
        second = self._overlay("two", (1,))
        self.scheduler.add_schedule(first)
        self.assertTrue(self._wait_started(count=1))
        self.scheduler.add_schedule(second)
        time.sleep(1.5)
        self.assertEqual(len(self.stream.started), 1)
        self.assertEqual(self.stream.started[0], (1, "one"))


class StreamTests(unittest.TestCase):
    def test_font_path_escaping(self):
        engine = StreamEngine.__new__(StreamEngine)
        self.assertEqual(
            engine._escape_font_path(r"C:\Windows\Fonts\arial.ttf"),
            r"C\:/Windows/Fonts/arial.ttf",
        )


if __name__ == "__main__":
    unittest.main()

# server.py
import re
from pathlib import Path
from typing import Callable, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from playlist_manager import PlaylistManager
from stream_engine import StreamEngine

_ALLOWED_HLS = re.compile(r"^(index\.m3u8|segment_\d+\.ts)$")


def create_app(playlist_manager: PlaylistManager,
               stream_engine: StreamEngine,
               base_url: Union[str, Callable[[], str]]):
    """
    base_url: строка "http://192.168.1.10:8000" или функция,
    возвращающая актуальный URL.
    """
    if callable(base_url):
        get_base_url = base_url
    else:
        fixed = base_url
        get_base_url = lambda: fixed

    app = FastAPI(title="IPTV Overlay Server", docs_url=None, redoc_url=None)

    @app.get("/playlist.m3u")
    async def get_playlist():
        content = playlist_manager.generate_server_m3u(base_url=get_base_url())
        return PlainTextResponse(
            content=content,
            media_type="application/x-mpegURL",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/stream/{channel_id}/{file_name}")
    async def serve_channel_file(channel_id: int, file_name: str):
        if channel_id <= 0:
            raise HTTPException(status_code=404, detail="Канал не найден")

        hls_dir = stream_engine.hls_root / str(channel_id)
        active = stream_engine.is_active(channel_id)

        if not active and file_name == "index.m3u8":
            channel = playlist_manager.get_channel_by_id(channel_id)
            if not channel:
                raise HTTPException(status_code=404, detail="Канал не найден")
            return RedirectResponse(url=channel.url, status_code=302)

        if not active:
            raise HTTPException(status_code=404, detail="Поток не активен")

        # Не отдаём ffmpeg.log и не позволяем выйти за пределы папки канала.
        if not _ALLOWED_HLS.fullmatch(file_name):
            raise HTTPException(status_code=404, detail="Файл не найден")

        root = hls_dir.resolve()
        file_path = (root / file_name).resolve()
        if root not in file_path.parents:
            raise HTTPException(status_code=404, detail="Файл не найден")
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="HLS-поток ещё не готов")

        media_type = ("application/vnd.apple.mpegurl"
                      if file_name.endswith(".m3u8") else "video/mp2t")
        return FileResponse(
            path=file_path,
            media_type=media_type,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return app

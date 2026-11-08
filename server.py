# server.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse, FileResponse
from playlist_manager import PlaylistManager
from stream_engine import StreamEngine


def create_app(playlist_manager: PlaylistManager,
               stream_engine: StreamEngine,
               base_url: str):
    app = FastAPI()

    @app.get("/playlist.m3u")
    async def get_playlist():
        """Отдаёт модифицированный M3U-плейлист."""
        m3u_content = playlist_manager.generate_server_m3u(base_url=base_url)
        return PlainTextResponse(
            content=m3u_content,
            media_type="application/x-mpegURL"
        )

    @app.get("/stream/{channel_id}/{file_name}")
    async def serve_channel_file(channel_id: int, file_name: str):
        """
        Обслуживает HLS-поток канала.
        - Если оверлей активен → отдаёт index.m3u8 и *.ts из папки канала.
        - Если неактивен и запрошен index.m3u8 → редирект на оригинал провайдера.
        """
        hls_dir = stream_engine.hls_root / str(channel_id)

        # 1. Оверлей неактивен, но клиент просит плейлист — редирект на оригинал
        if not stream_engine.is_active(channel_id) and file_name == "index.m3u8":
            channel = playlist_manager.get_channel_by_id(channel_id)
            if not channel:
                raise HTTPException(status_code=404, detail="Канал не найден")
            return RedirectResponse(url=channel.url, status_code=302)

        # 2. Оверлей неактивен, но клиент просит сегмент — поток не должен существовать
        if not stream_engine.is_active(channel_id):
            raise HTTPException(status_code=404, detail="Поток не активен")

        # 3. Оверлей активен — отдаём файл из папки канала
        if not hls_dir.exists():
            raise HTTPException(status_code=503, detail="HLS-поток ещё не готов")

        file_path = hls_dir / file_name
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"Файл {file_name} не найден")

        if file_name.endswith(".m3u8"):
            media_type = "application/vnd.apple.mpegurl"
        elif file_name.endswith(".ts"):
            media_type = "video/mp2t"
        else:
            media_type = "application/octet-stream"

        return FileResponse(file_path, media_type=media_type)

    return app
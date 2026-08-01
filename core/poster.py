"""
PosterManager — 海报下载的全局调度器（带有内存 RAM + 磁盘双级缓存）
"""
import os
import hashlib
import logging
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtGui import QPixmap, QPixmapCache
from PyQt6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply,
    QNetworkDiskCache
)

from core.config import ConfigManager

logger = logging.getLogger("PosterManager")


class PosterManager(QObject):
    _instance: Optional["PosterManager"] = None
    MAX_CONCURRENT = 4
    MAX_QUEUE = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self)
        cache.setCacheDirectory(os.path.join(ConfigManager.POSTER_CACHE_DIR, ".http_cache"))
        cache.setMaximumCacheSize(50 * 1024 * 1024)
        self._nam.setCache(cache)

        # 设置 Qt 全局 QPixmapCache 限制为 30MB，避免 RAM 无限膨胀
        QPixmapCache.setCacheLimit(30 * 1024)

        self._queue: list[tuple[int, str, list]] = []
        self._active: dict[str, QNetworkReply] = {}
        self._callbacks: dict[str, list[Callable]] = {}
        self._inflight: int = 0

    @classmethod
    def get(cls) -> "PosterManager":
        if cls._instance is None:
            cls._instance = PosterManager()
        return cls._instance

    # ── 公开 API ────────────────────────────────────────────────
    def request(self, url: str, callback: Callable[[QPixmap], None], priority: int = 50):
        if not url or not url.startswith("http"):
            return

        # 【性能核心优化 1】：内存 RAM 一级缓存检查（0.1ms 响应，零磁盘 I/O）
        pm_key = f"poster_ram_{url}"
        cached_pixmap = QPixmapCache.find(pm_key)
        if cached_pixmap:
            callback(cached_pixmap)
            return

        # 【性能核心优化 2】：磁盘二级缓存检查
        px = self._load_disk(url)
        if px is not None:
            QPixmapCache.insert(pm_key, px)  # 存入 RAM 缓存
            callback(px)
            return

        # 已在下载中
        if url in self._callbacks:
            self._callbacks[url].append(callback)
            return

        # 队列满时丢弃
        if len(self._queue) >= self.MAX_QUEUE:
            logger.warning("Poster queue full, dropping request: %s", url[:80])
            return

        self._callbacks[url] = [callback]
        self._enqueue(priority, url)
        self._pump()

    def cancel(self, url: str):
        self._callbacks.pop(url, None)
        self._queue = [(p, u, cbs) for (p, u, cbs) in self._queue if u != url]

    def reprioritize(self, url: str, priority: int):
        new_queue = []
        found = False
        for (p, u, cbs) in self._queue:
            if u == url:
                found = True
                self._enqueue_direct(priority, url, cbs)
            else:
                new_queue.append((p, u, cbs))
        if found:
            self._queue = new_queue
            self._queue.sort(key=lambda x: x[0])

    def clear_queue(self):
        """清空所有待处理的海报下载队列，并终止已发出的网络请求以节省带宽"""
        if self._queue or self._callbacks or self._active:
            logger.info("Clearing poster queue (pending=%d, active=%d)",
                        len(self._queue), len(self._active))
            self._queue.clear()
            self._callbacks.clear()
            # 【带宽优化】：中止正在进行的底层网络连接
            for url, reply in list(self._active.items()):
                try:
                    reply.abort()
                    reply.deleteLater()
                except Exception:
                    pass
            self._active.clear()
            self._inflight = 0

    # ── 内部实现 ─────────────────────────────────────────────────
    def _enqueue(self, priority: int, url: str):
        import bisect
        priorities = [p for (p, _, _) in self._queue]
        idx = bisect.bisect_right(priorities, priority)
        self._queue.insert(idx, (priority, url, self._callbacks[url]))

    def _enqueue_direct(self, priority: int, url: str, cbs: list):
        import bisect
        priorities = [p for (p, _, _) in self._queue]
        idx = bisect.bisect_right(priorities, priority)
        self._queue.insert(idx, (priority, url, cbs))

    def _pump(self):
        while self._queue and self._inflight < self.MAX_CONCURRENT:
            priority, url, cbs = self._queue.pop(0)
            if url not in self._callbacks or not self._callbacks[url]:
                continue
            self._start_download(url)

    def _start_download(self, url: str):
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(
            QNetworkRequest.Attribute.CacheLoadControlAttribute,
            QNetworkRequest.CacheLoadControl.PreferCache
        )
        req.setRawHeader(
            b"User-Agent",
            b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        reply = self._nam.get(req)
        self._active[url] = reply
        self._inflight += 1
        reply.finished.connect(lambda r=reply, u=url: self._on_finished(r, u))

    def _on_finished(self, reply: QNetworkReply, url: str):
        if url in self._active:
            self._active.pop(url, None)
            self._inflight = max(0, self._inflight - 1)

        callbacks = self._callbacks.pop(url, [])
        if callbacks:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = reply.readAll()
                px = QPixmap()
                if px.loadFromData(data):
                    pm_key = f"poster_ram_{url}"
                    QPixmapCache.insert(pm_key, px)  # 存入 RAM 缓存
                    self._save_disk(url, data.data())
                    for cb in callbacks:
                        try:
                            cb(px)
                        except Exception:
                            pass
                else:
                    empty = QPixmap()
                    for cb in callbacks:
                        try:
                            cb(empty)
                        except Exception:
                            pass
            else:
                logger.warning("Poster download failed: %s → %s", url[:80], reply.errorString())

        reply.deleteLater()
        self._pump()


    # ── 磁盘缓存 ───────────────────────────────────────
    @staticmethod
    def _cache_path(url: str) -> str:
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
        ext = os.path.splitext(url.split("?")[0])[1]
        if not ext or len(ext) > 5 or "/" in ext:
            ext = ".jpg"
        return os.path.join(ConfigManager.POSTER_CACHE_DIR, url_hash + ext)

    def _load_disk(self, url: str) -> Optional[QPixmap]:
        path = self._cache_path(url)
        if os.path.exists(path):
            px = QPixmap()
            if px.load(path):
                return px
        return None

    def _save_disk(self, url: str, data: bytes):
        path = self._cache_path(url)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            logger.error("Failed to save poster cache: %s", e)
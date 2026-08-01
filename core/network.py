# core/network.py
import json
import time
import ssl
import urllib.request
import urllib.parse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

from core.config import ConfigManager

logger = logging.getLogger("Network")


def _get_ssl_context():
    """忽略 SSL 证书校验，彻底解决采集站 Let's Encrypt / 过期证书导致的伪超时"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get_proxy_handler():
    """从设置中读取代理配置，返回 urllib ProxyHandler 或 None"""
    ud = ConfigManager.load_user_data()
    settings = ud.get("settings", {})
    proxy_type = settings.get("proxy_type", "无").upper()
    proxy_host = settings.get("proxy_host", "")
    proxy_port = settings.get("proxy_port", "")
    if proxy_type in ("HTTP", "SOCKS5") and proxy_host:
        try:
            port = int(proxy_port) if proxy_port else 1080
        except Exception:
            port = 1080
        scheme = "http" if proxy_type == "HTTP" else "socks5"
        proxy_url = f"{scheme}://{proxy_host}:{port}"
        return urllib.request.ProxyHandler({scheme: proxy_url})
    return None


def _get_opener():
    """创建统一包含代理与 SSL 忽略的 urllib opener"""
    proxy = _get_proxy_handler() or urllib.request.ProxyHandler()
    https = urllib.request.HTTPSHandler(context=_get_ssl_context())
    return urllib.request.build_opener(proxy, https)


def _get_timeout():
    """从设置中读取请求超时，默认 8 秒"""
    ud = ConfigManager.load_user_data()
    return ud.get("settings", {}).get("request_timeout", 8)


class SafeThread(QThread):
    finished = pyqtSignal(object)
    _active_threads = set()  # 全局强引用保持池，防止 Python GC 提前销毁后台线程导致 C++ 崩溃

    def __init__(self, target=None, parent=None):
        super().__init__(parent)
        self._abort = False
        self._target = target
        super().finished.connect(self._on_thread_finished)

    def start(self, *args, **kwargs):
        SafeThread._active_threads.add(self)
        super().start(*args, **kwargs)

    def _on_thread_finished(self):
        SafeThread._active_threads.discard(self)

    def request_abort(self):
        self._abort = True

    def run(self):
        if self._target:
            try:
                data = self._target()
                if not self._abort:
                    self.finished.emit(data if data is not None else {})
            except Exception as e:
                logger.error(f"SafeThread 执行发生未捕获异常: {e}")
                if not self._abort:
                    self.finished.emit({})


class NetworkCheckThread(QThread):
    result = pyqtSignal(bool)

    def run(self):
        try:
            opener = _get_opener()
            req = urllib.request.Request(
                "https://connect.rom.miui.com/generate_204",
                method="HEAD",
                headers=NetworkEngine.HEADERS
            )
            timeout = _get_timeout()
            opener.open(req, timeout=timeout)
            self.result.emit(True)
        except Exception:
            self.result.emit(False)


class FetchMoviesThread(SafeThread):
    finished = pyqtSignal(dict)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        data = NetworkEngine.fetch_json_with_retry(self.url)
        if not self._abort:
            self.finished.emit(data or {})


class FetchCategoriesThread(SafeThread):
    finished = pyqtSignal(dict)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        data = NetworkEngine.fetch_json_with_retry(self.url)
        if not self._abort:
            self.finished.emit(data or {})


class HealthCheckThread(SafeThread):
    result = pyqtSignal(dict)

    def __init__(self, sources):
        super().__init__()
        self.sources = sources

    def run(self):
        results = {}

        def check(name, url):
            timeout = _get_timeout()
            opener = _get_opener()

            # 1. 优先测试基础 API 响应速度（极速读取 64 字节，忽略 SSL 校验）
            t0 = time.time()
            try:
                req = urllib.request.Request(url, headers=NetworkEngine.HEADERS)
                with opener.open(req, timeout=timeout) as r:
                    r.read(64)
                return name, max(1, int((time.time() - t0) * 1000))
            except Exception:
                pass

            # 2. 如果基础 URL 失败，尝试 ac=list 参数备用路径
            t0 = time.time()
            try:
                ping_url = NetworkEngine.get_api_url(url, "ac=list")
                req = urllib.request.Request(ping_url, headers=NetworkEngine.HEADERS)
                with opener.open(req, timeout=timeout) as r:
                    r.read(64)
                return name, max(1, int((time.time() - t0) * 1000))
            except Exception:
                return name, -1

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(check, n, u): n for n, u in self.sources.items()}
            for fut in as_completed(futs):
                if self._abort:
                    break
                name, ms = fut.result()
                results[name] = ms
        if not self._abort:
            self.result.emit(results)


class NetworkEngine:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    @staticmethod
    def get_api_url(base, params):
        return f"{base}{'&' if '?' in base else '?'}{params}"

    @staticmethod
    def fetch_json_with_retry(url, retries=2, timeout=None):
        if timeout is None:
            timeout = _get_timeout()
        opener = _get_opener()
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=NetworkEngine.HEADERS)
                with opener.open(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="ignore"))
            except Exception as e:
                logger.warning(f"请求失败 (尝试 {attempt+1}/{retries+1}): {url} -> {e}")
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        return None

    @staticmethod
    def parse_all_routes(play_url_str):
        """兼容所有 CMS 采集站的精准播放线路解析器
        兼容格式：
        1. 标准: 线路1$$第1集$url1#第2集$url2$$$线路2$$...
        2. 简化: 红牛资源$$第一集$http...#第二集$http...
        3. 纯URL: http://xxx.m3u8
        """
        if not play_url_str:
            return []
        routes = []
        for ri, route_str in enumerate(play_url_str.split("$$$")):
            route_str = route_str.strip()
            if not route_str:
                continue
            parts = route_str.split("$$")
            route_name = f"线路 {ri+1}"
            raw_ep = ""

            if len(parts) >= 2:
                first_part = parts[0].strip()
                second_part = parts[1].strip()
                if "http" in second_part:
                    route_name = first_part or f"线路 {ri+1}"
                    raw_ep = second_part
                elif "http" in first_part:
                    raw_ep = first_part
                else:
                    last_http_idx = -1
                    for j, p in enumerate(parts):
                        if "http" in p:
                            last_http_idx = j
                            break
                    if last_http_idx >= 0:
                        name_parts = parts[:last_http_idx]
                        if name_parts:
                            route_name = "$$".join(name_parts).strip() or f"线路 {ri+1}"
                        raw_ep = "$$".join(parts[last_http_idx:])
                    else:
                        raw_ep = parts[-1]
            else:
                raw_ep = parts[0]

            episodes = []
            for part in raw_ep.split("#"):
                part = part.strip()
                if not part:
                    continue
                if "$" in part:
                    n, u = part.split("$", 1)
                    n = n.strip()
                    u = u.strip()
                    if u.startswith("http"):
                        episodes.append({"name": n or "正片", "url": u})
                elif part.startswith("http"):
                    episodes.append({"name": "正片", "url": part})

            if episodes:
                routes.append({"name": route_name, "episodes": episodes})
        return routes

    @staticmethod
    def fetch_tvbox_sources(url):
        data = NetworkEngine.fetch_json_with_retry(url)
        if not data and ("githubusercontent.com" in url or "github.com" in url):
            if not url.startswith("https://mirror.ghproxy.com/"):
                data = NetworkEngine.fetch_json_with_retry(
                    "https://mirror.ghproxy.com/" + url)
        if not data or "sites" not in data:
            return None
        result = {}
        for site in data["sites"]:
            if site.get("type") == 1:
                name = f"TV {site.get('name','未命名')}".strip()
                api = site.get("api", "").strip()
                if api:
                    result[name] = api
        return result if result else None
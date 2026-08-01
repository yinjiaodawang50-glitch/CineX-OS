# core/config.py
import os
import json
import logging
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class ConfigManager:
    SOURCES_FILE     = os.path.join(BASE_DIR, "sources.json")
    USER_DATA_FILE   = os.path.join(BASE_DIR, "user_data.json")
    POSTER_CACHE_DIR = os.path.join(BASE_DIR, "cache_posters")
    LOG_DIR          = os.path.join(BASE_DIR, "logs")
    FONTS_DIR        = os.path.join(BASE_DIR, "fonts")
    ASSETS_DIR       = os.path.join(BASE_DIR, "assets")

    # ─── 补全类级别的缓存变量 ───
    _user_data_cache = None

    @classmethod
    def init_env(cls):
        if not os.path.exists(cls.POSTER_CACHE_DIR):
            try: os.makedirs(cls.POSTER_CACHE_DIR)
            except: pass

        # ─── 日志初始化 ───
        if not os.path.exists(cls.LOG_DIR):
            try: os.makedirs(cls.LOG_DIR)
            except: pass

        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        logging.basicConfig(
            level=logging.INFO,          # 默认 INFO，稍后可能被用户设置覆盖
            format=log_format,
            handlers=[
                logging.FileHandler(os.path.join(cls.LOG_DIR, "app.log"), encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        logging.getLogger("PyQt6").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

        # 应用用户保存的日志级别
        try:
            ud = cls.load_user_data()
            level_str = ud.get("settings", {}).get("log_level", "INFO")
            level = getattr(logging, level_str.upper(), logging.INFO)
            logging.getLogger().setLevel(level)
        except Exception:
            pass

    @classmethod
    def _atomic_save_json(cls, filepath, data):
        """原子化写入 JSON，防止断电损坏"""
        dirname = os.path.dirname(os.path.abspath(filepath)) or "."
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dirname, delete=False) as tf:
                json.dump(data, tf, indent=4, ensure_ascii=False)
                tf.flush()
                os.fsync(tf.fileno())
                temp_name = tf.name
            os.replace(temp_name, filepath)
        except Exception as e:
            logging.error(f"保存 JSON 配置文件失败 [{filepath}]: {e}")

    @classmethod
    def load_sources(cls):
        default = {
            "量子资源": "https://cj.lziapi.com/api.php/provide/vod/from/lzm3u8/",
            "红牛资源": "https://www.hongniuzy2.com/api.php/provide/vod/from/hnm3u8/",
            "暴风资源": "https://bfzyapi.com/api.php/provide/vod/",
            "非凡资源": "https://api.ffzyapi.com/api.php/provide/vod/",
        }
        if not os.path.exists(cls.SOURCES_FILE):
            cls.save_sources(default); return default
        try:
            with open(cls.SOURCES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default

    @classmethod
    def save_sources(cls, data):
        cls._atomic_save_json(cls.SOURCES_FILE, data)

    @classmethod
    def load_user_data(cls):
        if cls._user_data_cache is not None:
            return cls._user_data_cache

        default = {"favorites": [], "history": [], "progress": {}, "last_route": {}, "search_history": []}
        if not os.path.exists(cls.USER_DATA_FILE):
            cls.save_user_data(default)
            cls._user_data_cache = default
            return default
        try:
            with open(cls.USER_DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                for key in ["progress", "last_route", "search_history"]:
                    if key not in d:
                        d[key] = {} if key != "search_history" else []
                cls._user_data_cache = d
                return d
        except:
            cls._user_data_cache = default
            return default

    @classmethod
    def save_user_data(cls, data):
        cls._user_data_cache = data  # 更新缓存
        cls._atomic_save_json(cls.USER_DATA_FILE, data)

    @classmethod
    def clean_cache(cls, max_size_mb=None):
        cache_dir = cls.POSTER_CACHE_DIR
        if not os.path.exists(cache_dir):
            return
        # 如果没有传入参数，则从用户设置读取
        if max_size_mb is None:
            ud = cls.load_user_data()
            max_size_mb = ud.get("settings", {}).get("cache_auto_clean_mb", 500)
        total_size = 0
        files = []
        for f in os.listdir(cache_dir):
            path = os.path.join(cache_dir, f)
            if os.path.isfile(path):
                size = os.path.getsize(path)
                total_size += size
                files.append((path, os.path.getmtime(path), size))
        if total_size > max_size_mb * 1024 * 1024:
            files.sort(key=lambda x: x[1])
            to_delete = int(len(files) * 0.2)
            for path, _, _ in files[:to_delete]:
                try: os.remove(path)
                except: pass
            logging.info(f"缓存清理：删除了 {to_delete} 个文件")
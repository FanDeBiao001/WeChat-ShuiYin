"""酷狗音乐 MV 与歌曲分享解析器。"""

import hashlib
import json
import re
import time
from urllib.parse import parse_qs, urlparse

from configs.logging_config import get_logger
from src.parser_factory import register_parser
from src.parsers.base_parser import BaseParser


logger = get_logger(__name__)


@register_parser("酷狗音乐")
class KugouMusicParser(BaseParser):
    """解析酷狗移动端公开 MV，以及未受限歌曲分享页。"""

    MV_API_URL = "https://m3ws.kugou.com/api/v1/mv/infov2"
    SIGNATURE_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
    MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
        "Mobile/15E148 Safari/604.1"
    )

    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {"User-Agent": self.MOBILE_UA, "Referer": "https://m.kugou.com/"}
        self.title = ""
        self.cover_url = None
        self.author = {"nickname": "", "author_id": "", "avatar": ""}
        self.video_list = []
        self.audio_url = None
        self.media_type, self.content_id = self._detect_media(real_url)
        self._parse()

    @staticmethod
    def _detect_media(url):
        parsed = urlparse(url or "")
        params = parse_qs(parsed.query)
        path = parsed.path
        path_lower = path.lower()
        desktop_mv = re.search(r"/mvweb/html/mv_([0-9a-f]{32})\.html", path, re.I)
        if desktop_mv:
            return "mv", desktop_mv.group(1)
        if "/mv" in path_lower or "/mv/" in path_lower:
            return "mv", (params.get("hash") or [None])[0]
        chain_path_match = re.search(r"/share/([a-zA-Z0-9_-]+)\.html", path)
        if chain_path_match and chain_path_match.group(1).lower() not in ("song", "default", "index"):
            return "song", chain_path_match.group(1)
        chain = (params.get("chain") or [None])[0]
        if chain:
            return "song", chain
        if "/share/song" in path_lower or "/song" in path_lower or "/share/default" in path_lower:
            return "song", chain
        return None, None

    def _parse(self):
        if self.media_type == "mv" and self.content_id:
            self._parse_mv()
        elif self.media_type == "song":
            self._parse_song_page()

    def _parse_mv(self):
        timestamp = str(int(time.time() * 1000))
        params = {
            "cmd": "100",
            "hash": self.content_id,
            "ext": "mp4",
            "ismp3": "1",
            "ssl": "1",
            "srcappid": "2919",
            "clientver": "20000",
            "clienttime": timestamp,
            "mid": timestamp,
            "uuid": timestamp,
            "dfid": "-",
        }
        source = self.SIGNATURE_SALT + "".join(
            f"{key}={params[key]}" for key in sorted(params)
        ) + self.SIGNATURE_SALT
        params["signature"] = hashlib.md5(source.encode()).hexdigest()
        try:
            response = self.session.get(
                self.MV_API_URL, params=params, headers=self.headers, timeout=10
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("Failed to fetch Kugou MV data: %s", exc)
            return
        if not isinstance(payload, dict) or payload.get("errcode") not in (None, 0):
            return

        self.title = payload.get("songname") or ""
        cover = payload.get("mvicon")
        self.cover_url = cover.replace("{size}", "400") if isinstance(cover, str) else cover
        self.author = {
            "nickname": payload.get("singer") or "",
            "author_id": str(payload.get("id") or ""),
            "avatar": "",
        }
        self.video_list = self._extract_mv_streams(payload.get("mvdata") or {})

    def _parse_song_page(self):
        target_url = self.real_url
        if self.content_id:
            target_url = f"https://m.kugou.com/share/song.html?chain={self.content_id}"

        try:
            response = self.session.get(target_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            self.html_content = response.text
        except Exception as exc:
            logger.warning("Failed to fetch Kugou song page: %s", exc)
            if target_url != self.real_url:
                try:
                    response = self.session.get(self.real_url, headers=self.headers, timeout=10)
                    response.raise_for_status()
                    self.html_content = response.text
                except Exception:
                    return
            else:
                return

        match = re.search(r"var\s+phpParam\s*=\s*(\{.*?\});", self.html_content or "", re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(1))
                data = ((payload.get("song_info") or {}).get("data") or {})
                self.title = data.get("songName") or data.get("fileName") or ""
                cover = data.get("album_img") or data.get("imgUrl")
                self.cover_url = cover.replace("{size}", "400") if isinstance(cover, str) else cover
                authors = data.get("authors") or []
                if authors and isinstance(authors[0], dict):
                    author = authors[0]
                    self.author = {
                        "nickname": author.get("author_name") or author.get("name") or "",
                        "author_id": str(author.get("author_id") or author.get("id") or ""),
                        "avatar": (author.get("avatar") or "").replace("{size}", "400"),
                    }
                elif data.get("singerName"):
                    self.author["nickname"] = data["singerName"]

                # 付费、试听或平台明确报错的地址不能作为完整音频返回。
                if not (data.get("error") or data.get("pay_type") not in (None, 0, "0")):
                    url = data.get("url")
                    if self._valid_url(url):
                        self.audio_url = url
                return
            except Exception as e:
                logger.debug("Failed to parse phpParam: %s", e)

        match_smarty = re.search(r"var\s+dataFromSmarty\s*=\s*(\[.*?\]);", self.html_content or "", re.DOTALL)
        if match_smarty:
            try:
                smarty_list = json.loads(match_smarty.group(1))
                if smarty_list and isinstance(smarty_list[0], dict):
                    item = smarty_list[0]
                    self.title = item.get("song_name") or item.get("audio_name") or ""
                    self.author = {
                        "nickname": item.get("author_name") or "",
                        "author_id": str(item.get("author_id") or ""),
                        "avatar": "",
                    }
            except Exception as e:
                logger.debug("Failed to parse dataFromSmarty: %s", e)

    @staticmethod
    def _extract_mv_streams(mvdata):
        streams = []
        for rank, key in enumerate(("sq", "rq", "le", "sd")):
            item = mvdata.get(key) or {}
            if not isinstance(item, dict):
                continue
            candidates = [item.get("downurl"), *(item.get("backupdownurl") or [])]
            url = next((value for value in candidates if KugouMusicParser._valid_url(value)), None)
            if url:
                streams.append((rank, url))
        return [url for _, url in sorted(streams, key=lambda item: item[0])]

    @staticmethod
    def _valid_url(url):
        return isinstance(url, str) and url.startswith(("http://", "https://"))

    def get_real_video_url(self):
        return None

    def get_video_list(self):
        return self.video_list

    def get_audio_url(self):
        return self.audio_url

    def get_title_content(self):
        return self.title

    def get_cover_photo_url(self):
        return self.cover_url

    def get_author_info(self):
        return self.author

"""QQ 音乐歌曲与 MV 分享解析器。"""

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

from configs.logging_config import get_logger
from src.parser_factory import register_parser
from src.parsers.base_parser import BaseParser


logger = get_logger(__name__)


@register_parser("QQ音乐")
class QQMusicParser(BaseParser):
    """解析 QQ 音乐公开歌曲与 MV 页面，返回音频流、歌词或多清晰度视频。"""

    API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
        "Mobile/15E148 Safari/604.1"
    )
    DESKTOP_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {
            "User-Agent": self.MOBILE_UA,
            "Referer": "https://y.qq.com/",
        }
        self.title = ""
        self.cover_url = None
        self.author = {"nickname": "", "author_id": "", "avatar": ""}
        self.video_list = []
        self.audio_url = None
        self.subtitles = None

        self.media_type, self.vid, self.songmid, self.songid = self._detect_media(real_url)
        self._parse_page()
        if self.media_type == "song" or self.songmid or self.songid:
            self._fetch_song_data()
        elif self.media_type == "mv" or self.vid:
            self._fetch_mv_data()

    @staticmethod
    def _detect_media(url):
        parsed = urlparse(url or "")
        params = parse_qs(parsed.query)
        path = parsed.path

        # 检查 MV
        if vid := (params.get("vid") or [None])[0]:
            return "mv", vid, None, None
        if match := re.search(r"/(?:mv|mvDetail)/([A-Za-z0-9]+)(?:/|$)", path):
            return "mv", match.group(1), None, None

        # 检查歌曲
        songmid = (params.get("songmid") or params.get("songMid") or [None])[0]
        songid = (params.get("songid") or params.get("songId") or params.get("id") or [None])[0]
        if songmid or songid:
            return "song", None, songmid, songid

        if match := re.search(r"/(?:songDetail|song)/([A-Za-z0-9]+)(?:/|$)", path):
            return "song", None, match.group(1), None

        if "playsong" in path:
            return "song", None, songmid, songid

        return None, None, None, None

    def _parse_page(self):
        """抓取页面 HTML 并尝试解析 SSR 数据，用于补充元数据或识别短链。"""
        try:
            response = self.session.get(
                self.real_url,
                headers=self.headers,
                allow_redirects=True,
                timeout=10,
            )
            response.raise_for_status()
            self.html_content = response.text
            final_url = response.url or self.real_url
        except Exception as exc:
            logger.warning("Failed to fetch QQMusic page: %s", exc)
            return

        # 如果初始未检测到媒体类型，从重定向后的 URL 再次检测
        if not self.media_type and not self.vid and not self.songmid and not self.songid:
            self.media_type, self.vid, self.songmid, self.songid = self._detect_media(final_url)

        match = re.search(
            r"window\.__ssrFirstPageData__\s*=\s*(\"(?:\\.|[^\"\\])*\")",
            self.html_content or "",
            re.DOTALL,
        )
        if not match:
            return

        try:
            encoded_payload = json.loads(match.group(1))
            payload = json.loads(encoded_payload)
            data = payload.get("data") or payload

            # MV 页面 SSR 结构
            video = data.get("video")
            if video and isinstance(video, dict):
                self.media_type = self.media_type or "mv"
                self._apply_mv_metadata(video)
                creators = data.get("creator") or []
                if creators and isinstance(creators[0], dict):
                    creator = creators[0]
                    self.author = {
                        "nickname": creator.get("nick") or creator.get("name") or "",
                        "author_id": str(creator.get("mid") or creator.get("uin") or ""),
                        "avatar": creator.get("avatar") or creator.get("headurl") or "",
                    }

            # 歌曲页面 SSR 结构
            song = data.get("song")
            if song and isinstance(song, dict):
                self.media_type = self.media_type or "song"
                self.songmid = self.songmid or song.get("mid")
                self.songid = self.songid or (str(song.get("id")) if song.get("id") else None)
                self._apply_song_metadata(song)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to parse QQMusic SSR payload: %s", exc)

    def _fetch_song_data(self):
        """调用 QQ 音乐 PC Web 协议接口获取歌曲信息、音频播放地址及歌词。"""
        numeric_id = 0
        if self.songid:
            try:
                numeric_id = int(self.songid)
            except (ValueError, TypeError):
                numeric_id = 0

        payload = {
            "comm": {
                "cv": 4747474,
                "ct": 24,
                "format": "json",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "notice": 0,
                "platform": "yqq.json",
                "needNewCode": 1,
                "uin": 0,
            },
            "songinfo": {
                "module": "music.pf_song_detail_svr",
                "method": "get_song_detail_yqq",
                "param": {
                    "song_mid": self.songmid or "",
                    "song_id": numeric_id,
                },
            },
            "playUrl": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "guid": "2333333333",
                    "songmid": [self.songmid] if self.songmid else [],
                    "songtype": [0],
                    "uin": "0",
                    "loginflag": 1,
                    "platform": "20",
                },
            },
            "lyric": {
                "module": "music.musichallSong.PlayLyricInfo",
                "method": "GetPlayLyricInfo",
                "param": {
                    "songMID": self.songmid or "",
                    "songID": numeric_id,
                },
            },
        }

        try:
            response = self.session.post(
                self.API_URL,
                json=payload,
                headers={
                    "User-Agent": self.DESKTOP_UA,
                    "Referer": "https://y.qq.com/",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            logger.warning("Failed to fetch QQMusic song data: %s", exc)
            return

        # 1. 歌曲元数据
        track_info = ((result.get("songinfo") or {}).get("data") or {}).get("track_info") or {}
        if track_info:
            self._apply_song_metadata(track_info)

        # 2. 音频直链
        play_data = (result.get("playUrl") or {}).get("data") or {}
        sips = play_data.get("sip") or []
        midurlinfo = play_data.get("midurlinfo") or []
        if midurlinfo and isinstance(midurlinfo[0], dict):
            purl = midurlinfo[0].get("purl")
            if purl and sips:
                sip = next((s for s in sips if s.startswith("http")), sips[0])
                self.audio_url = f"{sip}{purl}"

        # 3. 歌词解析
        lyric_b64 = ((result.get("lyric") or {}).get("data") or {}).get("lyric")
        if lyric_b64 and isinstance(lyric_b64, str):
            try:
                decoded = base64.b64decode(lyric_b64).decode("utf-8", errors="ignore")
                self.subtitles = self._parse_lrc(decoded)
            except Exception as exc:
                logger.debug("Failed to decode QQMusic lyrics: %s", exc)

    def _apply_song_metadata(self, track):
        if not isinstance(track, dict):
            return
        self.title = self.title or track.get("name") or track.get("title") or ""
        album = track.get("album") or {}
        album_mid = album.get("mid") or (album.get("pmid", "").rstrip("_1") if isinstance(album.get("pmid"), str) else None)
        if album_mid and not self.cover_url:
            self.cover_url = f"https://y.qq.com/music/photo_new/T002R800x800M000{album_mid}.jpg"

        singers = track.get("singer") or track.get("singers") or []
        if singers and isinstance(singers[0], dict) and not self.author["nickname"]:
            singer = singers[0]
            singer_mid = singer.get("mid") or (singer.get("pmid", "").rstrip("_1") if isinstance(singer.get("pmid"), str) else "")
            avatar = f"https://y.qq.com/music/photo_new/T001R300x300M000{singer_mid}.jpg" if singer_mid else ""
            self.author = {
                "nickname": singer.get("name") or singer.get("title") or "",
                "author_id": str(singer.get("mid") or singer.get("id") or ""),
                "avatar": avatar,
            }

    @staticmethod
    def _parse_lrc(lrc_text):
        subtitles = []
        for line in lrc_text.splitlines():
            match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", line.strip())
            if match and match.group(3).strip():
                start = int(match.group(1)) * 60 + float(match.group(2))
                subtitles.append({"start": round(start, 3), "text": match.group(3).strip()})
        return subtitles or None

    def _fetch_mv_data(self):
        if not self.vid:
            return
        payload = {
            "comm": {"ct": 24, "cv": 0},
            "mvInfo": {
                "module": "video.VideoDataServer",
                "method": "get_video_info_batch",
                "param": {
                    "vidlist": [self.vid],
                    "required": [
                        "vid",
                        "type",
                        "sid",
                        "cover_pic",
                        "duration",
                        "singers",
                        "video_pay",
                        "hint",
                        "code",
                        "msg",
                        "name",
                        "desc",
                        "playcnt",
                        "pubdate",
                        "isfav",
                        "gmid",
                    ],
                },
            },
            "mvUrl": {
                "module": "gosrf.Stream.MvUrlProxy",
                "method": "GetMvUrls",
                "param": {"vids": [self.vid], "request_typet": 10001},
            },
        }
        try:
            response = self.session.post(
                self.API_URL,
                json=payload,
                headers={**self.headers, "Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            logger.warning("Failed to fetch QQMusic MV data: %s", exc)
            return

        metadata = (((result.get("mvInfo") or {}).get("data") or {}).get(self.vid) or {})
        self._apply_mv_metadata(metadata)
        stream_data = (((result.get("mvUrl") or {}).get("data") or {}).get(self.vid) or {})
        self.video_list = self._extract_streams(stream_data)

    def _apply_mv_metadata(self, video):
        if not isinstance(video, dict):
            return
        self.title = self.title or video.get("name") or video.get("raw_name") or ""
        self.cover_url = self.cover_url or video.get("cover_pic") or video.get("first_frame_pic")

        singers = video.get("singers") or video.get("related_singers") or []
        if singers and isinstance(singers[0], dict) and not self.author["nickname"]:
            singer = singers[0]
            self.author = {
                "nickname": singer.get("name") or singer.get("title") or "",
                "author_id": str(singer.get("mid") or singer.get("id") or ""),
                "avatar": singer.get("picurl") or singer.get("avatar") or "",
            }

        if not self.author["nickname"] and video.get("uploader_nick"):
            self.author = {
                "nickname": video.get("uploader_nick") or "",
                "author_id": str(video.get("uploader_encuin") or video.get("uploader_uin") or ""),
                "avatar": video.get("uploader_headurl") or "",
            }

    @staticmethod
    def _extract_streams(stream_data):
        streams = []
        for kind in ("mp4", "hls"):
            for item in stream_data.get(kind) or []:
                if not isinstance(item, dict) or item.get("code") not in (None, 0):
                    continue
                candidates = item.get("url") or item.get("freeflow_url") or item.get("comm_url") or []
                if isinstance(candidates, str):
                    candidates = [candidates]
                url = next(
                    (value for value in candidates if isinstance(value, str) and value.startswith(("http://", "https://"))),
                    None,
                )
                if not url and isinstance(item.get("m3u8"), str) and item["m3u8"].startswith(("http://", "https://")):
                    url = item["m3u8"]
                if url:
                    quality = item.get("filetype") or item.get("newFileType") or item.get("fileSize") or 0
                    streams.append((quality, url))

        streams.sort(key=lambda value: value[0], reverse=True)
        return list(dict.fromkeys(url for _, url in streams))

    def get_real_video_url(self):
        return None

    def get_video_list(self):
        return self.video_list

    def get_audio_url(self):
        return self.audio_url

    def get_subtitles(self):
        return self.subtitles

    def get_title_content(self):
        return self.title

    def get_cover_photo_url(self):
        return self.cover_url

    def get_author_info(self):
        return self.author


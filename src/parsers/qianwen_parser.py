from src.parser_factory import register_parser
import json
import re
import urllib.parse

from configs.general_constants import USER_AGENT_M
from configs.logging_config import get_logger
from src.parsers.base_parser import BaseParser

logger = get_logger(__name__)


@register_parser("通义千问")
class QianwenParser(BaseParser):
    """通义千问（Qwen）及夸克 AI 生成作品与对话分享解析器。"""

    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {
            "User-Agent": USER_AGENT_M[0],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self.title = None
        self.cover_url = None
        self.video_url = None
        self.video_list = []
        self.image_list = []
        self.author = None
        self._parse()

    def _parse(self):
        if not self.real_url:
            return
        parsed = urllib.parse.urlparse(self.real_url)
        q = urllib.parse.parse_qs(parsed.query)
        share_id = q.get('share_id', [None])[0] or q.get('shareId', [None])[0]
        if not share_id:
            m = re.search(r'/share/chat/([a-zA-Z0-9_-]+)', parsed.path)
            if m:
                share_id = m.group(1)
        biz_id = q.get('biz_id', [None])[0] or q.get('bizId', [None])[0] or 'ai_qwen'

        # 1. 尝试通义千问官方 chat2-api 接口（适用于 SPA 架构的 share/chat 分享）
        if share_id:
            try:
                api_url = "https://chat2-api.qianwen.com/api/v1/share/info?pr=qwen&fr=mac"
                resp = self.session.post(
                    api_url,
                    json={"share_id": share_id, "biz_id": biz_id},
                    headers={"User-Agent": USER_AGENT_M[0], "Content-Type": "application/json"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    api_json = resp.json()
                    if api_json.get("code") == 0 and isinstance(api_json.get("data"), dict):
                        data = api_json["data"]
                        if data.get("session") or data.get("title"):
                            self._extract_from_data(data)
                            if self.image_list or self.video_list or self.video_url:
                                return
            except Exception as exc:
                logger.warning("Failed to fetch Qianwen share info via API: %s", exc)

        # 2. 回退到 HTML SSR 解析（适用于 activity.qianwen.com 等 AI Studio 页面）
        try:
            resp = self.session.get(self.real_url, headers=self.headers, timeout=10)
            resp.raise_for_status()

            marker = "window.__INITIAL_PROPS__"
            pos = resp.text.find(marker)
            if pos == -1:
                # 尝试从 OpenGraph Meta 标签提取标题与封面
                m_title = re.search(r'<meta property="og:title" content="([^"]+)"', resp.text)
                if m_title:
                    self.title = m_title.group(1)
                m_cover = re.search(r'<meta property="og:image" content="([^"]+)"', resp.text)
                if m_cover:
                    self.cover_url = m_cover.group(1)
                return

            sub = resp.text[pos + len(marker):].lstrip(" =")
            end_pos = sub.find("</script>")
            if end_pos != -1:
                sub = sub[:end_pos].rstrip().rstrip(";")

            data = json.loads(sub)
            raw_initial = data.get("initialData", {})
            if isinstance(raw_initial, str):
                if raw_initial.startswith("%"):
                    raw_initial = urllib.parse.unquote(raw_initial)
                try:
                    raw_initial = json.loads(raw_initial)
                except Exception:
                    pass

            if isinstance(raw_initial, dict):
                initial_data = raw_initial.get("data") if isinstance(raw_initial.get("data"), dict) else raw_initial
            else:
                initial_data = {}

            self._extract_from_data(initial_data)

        except Exception as exc:
            logger.warning("Failed to parse Qianwen share page: %s", exc)

    def _extract_from_data(self, data):
        """从解析后的字典中统一提取标题、作者、图片及视频。"""
        if not isinstance(data, dict):
            return

        self.title = (
            data.get("title")
            or data.get("shareSubtitle")
            or data.get("shareTitle")
        )
        session = data.get("session") if isinstance(data.get("session"), dict) else {}
        if not self.title and session:
            self.title = session.get("title")
            if not self.title and isinstance(session.get("record_list"), list) and session["record_list"]:
                rec0 = session["record_list"][0]
                if isinstance(rec0, dict):
                    query = rec0.get("query")
                    if isinstance(query, str):
                        self.title = query
                    elif isinstance(query, dict):
                        self.title = query.get("content") or query.get("text")
                    elif isinstance(rec0.get("request_messages"), list) and rec0["request_messages"]:
                        m0 = rec0["request_messages"][0]
                        if isinstance(m0, dict) and m0.get("content"):
                            self.title = m0.get("content")

        creator = data.get("creator") or data.get("content", {}).get("creator", {})
        if isinstance(creator, dict) and creator:
            author_id = creator.get("authorId") or creator.get("uid")
            self.author = {
                "nickname": creator.get("nick") or "",
                "author_id": str(author_id) if author_id else "",
                "avatar": creator.get("avatar") or "",
            }

        images_raw = data.get("images") or []
        if not images_raw and data.get("image"):
            images_raw = [data["image"]]

        img_urls = []
        for img in images_raw:
            if isinstance(img, dict):
                url = img.get("url") or img.get("downloadUrl")
                if url:
                    img_urls.append(url)
            elif isinstance(img, str):
                img_urls.append(img)

        video_urls = []
        play_info = data.get("playInfo")
        if isinstance(play_info, dict):
            v_url = play_info.get("url") or play_info.get("downloadUrl")
            if v_url:
                video_urls.append(v_url)

        deep_videos, deep_images = self._deep_extract_media(data)
        if not video_urls:
            video_urls.extend(deep_videos)
        if not img_urls:
            img_urls.extend(deep_images)

        self.image_list = list(dict.fromkeys(img_urls))
        self.video_list = list(dict.fromkeys(video_urls))

    @staticmethod
    def _is_video_url(url):
        if not isinstance(url, str) or not url.startswith("http"):
            return False
        clean_url = url.replace("\\u0026", "&")
        path = urllib.parse.unquote(urllib.parse.urlsplit(clean_url).path).lower()
        return path.endswith((".mp4", ".mov", ".m4v", ".webm")) or "/video/" in path or ".mp4" in path

    @staticmethod
    def _is_image_url(url):
        if not isinstance(url, str) or not url.startswith("http"):
            return False
        clean_url = url.replace("\\u0026", "&")
        path = urllib.parse.unquote(urllib.parse.urlsplit(clean_url).path).lower()
        return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) or ".jpg" in path or ".png" in path

    def _deep_extract_media(self, data):
        videos = []
        images = []
        seen = set()

        def _walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("url", "downloadUrl", "playUrl") and isinstance(v, str) and v.startswith("http"):
                        clean_v = v.replace("\\u0026", "&")
                        if clean_v not in seen:
                            seen.add(clean_v)
                            if self._is_video_url(clean_v):
                                videos.append(clean_v)
                            elif self._is_image_url(clean_v):
                                images.append(clean_v)
                    else:
                        _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(data)
        return videos, images

    def get_real_video_url(self):
        return self.video_url

    def get_video_list(self):
        return self.video_list

    def get_title_content(self):
        return self.title or ""

    def get_cover_photo_url(self):
        return self.cover_url

    def get_author_info(self):
        return self.author

    def get_image_list(self):
        return self.image_list

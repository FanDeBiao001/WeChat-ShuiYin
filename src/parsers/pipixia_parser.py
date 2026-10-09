from src.parser_factory import register_parser

import json
import random
import re
from urllib.parse import unquote, urljoin

from src.parsers.base_parser import BaseParser
from configs.general_constants import USER_AGENT_PC
from configs.logging_config import get_logger

logger = get_logger(__name__)


@register_parser("皮皮虾")
class PipixiaParser(BaseParser):
    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {
            "User-Agent": random.choice(USER_AGENT_PC),
            "Referer": "https://h5.pipix.com/",
        }
        self.page_title = ""
        self.item = {}
        self.data = self.fetch_html_data()

    def _extract_item_from_html(self, html_text):
        """从 H5 页面中解析注入的 script JSON 数据"""
        if not html_text:
            return None
        for s in re.findall(r'<script[^>]*>(.*?)</script>', html_text, re.DOTALL):
            st = s.strip()
            if '%22ppxItemDetail%22' in st or '%22itemId%22' in st or '%7B%22' in st:
                try:
                    payload = json.loads(unquote(st))
                    item = payload.get('ppxItemDetail', {}).get('item')
                    if item:
                        if not self.page_title:
                            self.page_title = payload.get('seoTDK', {}).get('title', '')
                            if self.page_title:
                                self.page_title = re.sub(r"\s*-\s*皮皮虾\s*$", "", self.page_title).strip()
                        return item
                except Exception:
                    pass
            elif 'ppxItemDetail' in st:
                try:
                    payload = json.loads(st)
                    item = payload.get('ppxItemDetail', {}).get('item')
                    if item:
                        if not self.page_title:
                            self.page_title = payload.get('seoTDK', {}).get('title', '')
                            if self.page_title:
                                self.page_title = re.sub(r"\s*-\s*皮皮虾\s*$", "", self.page_title).strip()
                        return item
                except Exception:
                    pass
        return None

    def fetch_html_data(self):
        resp = None
        try:
            resp = self.session.get(
                self.real_url, headers=self.headers, allow_redirects=False, timeout=10
            )
            raw_text = getattr(resp, "text", None)
            self.html_content = raw_text if isinstance(raw_text, str) else ""
        except Exception as e:
            logger.warning(f"Failed to fetch HTML content for Pipixia: {e}")
            self.html_content = ""

        if self.html_content:
            self.item = self._extract_item_from_html(self.html_content) or {}
            if not self.page_title:
                self.page_title = self._fetch_page_title(self.html_content)
            if self.item:
                return {"data": {"item": self.item}}

        # 兜底：通过 location / cell_comment API 获取
        location_url = ""
        if resp and hasattr(resp, "headers") and hasattr(resp.headers, "get"):
            location_val = resp.headers.get("location")
            if isinstance(location_val, str):
                location_url = location_val
        if not location_url and resp and isinstance(getattr(resp, "url", None), str):
            location_url = resp.url
        if not location_url:
            location_url = self.real_url
        location_url = urljoin(self.real_url, location_url)
        video_id = location_url.split("?")[0].split("/")[-1]
        if not video_id.isdigit():
            return {}

        req_url = f"https://api.pipix.com/bds/cell/cell_comment/?offset=0&cell_type=1&api_version=1&cell_id={video_id}&ac=wifi&channel=huawei_1319_64&aid=1319&app_name=super"
        try:
            api_resp = self.session.get(req_url, headers=self.headers, timeout=10)
            if hasattr(api_resp, "raise_for_status"):
                api_resp.raise_for_status()
            data = api_resp.json() if hasattr(api_resp, "json") else {}
            cell_comments = data.get("data", {}).get("cell_comments", [])
            if cell_comments:
                self.item = cell_comments[0].get("comment_info", {}).get("item", {})
            if not self.page_title:
                self.page_title = self._fetch_page_title(location_url)
            return data
        except Exception:
            return {}

    def _fetch_page_title(self, target):
        if not target:
            return ""
        if isinstance(target, str) and ("<html" in target.lower() or "<meta" in target.lower()):
            html_text = target
        else:
            try:
                resp = self.session.get(target, headers=self.headers, timeout=10)
                html_text = getattr(resp, "text", "")
            except Exception:
                return ""
        if not isinstance(html_text, str):
            return ""
        match = re.search(
            r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']',
            html_text,
            re.IGNORECASE,
        )
        title = match.group(1) if match else ""
        return re.sub(r"\s*-\s*皮皮虾\s*$", "", title).strip()

    def _get_item(self):
        if self.item:
            return self.item
        try:
            item = self.data["data"]["item"]
            if item:
                return item
        except (KeyError, TypeError):
            pass
        try:
            return self.data["data"]["cell_comments"][0]["comment_info"]["item"]
        except (KeyError, TypeError, IndexError):
            return {}

    def get_real_video_url(self):
        try:
            item = self._get_item()
            video = item.get("video")
            if not video or not isinstance(video, dict):
                return None
            for key in ("video_download", "video_high", "video_fallback", "video_mid", "video_low"):
                v_obj = video.get(key)
                if isinstance(v_obj, dict) and v_obj.get("url_list"):
                    return v_obj["url_list"][0]["url"]
            return None
        except Exception:
            return None

    def get_image_list(self):
        try:
            item = self._get_item()
            images = []
            note = item.get("note")
            if isinstance(note, dict) and note.get("multi_image"):
                for img in note["multi_image"]:
                    if isinstance(img, dict) and img.get("url_list"):
                        images.append(img["url_list"][0]["url"])
            return images
        except Exception:
            return []

    def get_cover_photo_url(self):
        try:
            item = self._get_item()
            cover = item.get("cover")
            if isinstance(cover, dict) and cover.get("url_list"):
                return cover["url_list"][0]["url"]
            video = item.get("video")
            if isinstance(video, dict):
                cover_img = video.get("cover_image")
                if isinstance(cover_img, dict) and cover_img.get("url_list"):
                    return cover_img["url_list"][0]["url"]
            return None
        except Exception:
            return None

    def get_title_content(self):
        return self.page_title or None

    def get_description(self):
        try:
            item = self._get_item()
            content = item.get("content", "")
            return content or None
        except Exception:
            return None

    def get_author_info(self):
        try:
            item = self._get_item()
            author = item.get("author") or {}
            avatar_urls = author.get("avatar", {}).get("download_list", []) or author.get("avatar", {}).get("url_list", [])
            avatar_url = ""
            if avatar_urls:
                first = avatar_urls[0]
                avatar_url = first.get("url", "") if isinstance(first, dict) else str(first)
            return {
                "nickname": author.get("name", ""),
                "author_id": str(author.get("id", "") or author.get("id_str", "")),
                "avatar": avatar_url,
            }
        except Exception:
            return {}

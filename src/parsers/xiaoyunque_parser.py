from src.parser_factory import register_parser
"""小云雀AI (xiaoyunque.jianying.com) 分享解析器。"""

from urllib.parse import parse_qs, urlparse

from configs.logging_config import get_logger
from src.parsers.base_parser import BaseParser

logger = get_logger(__name__)


@register_parser("小云雀AI")
class XiaoyunqueParser(BaseParser):
    """通过小云雀官方接口解析 xiaoyunque.jianying.com 分享链接。"""

    API_URL = "https://xiaoyunque.jianying.com/luckycat/cn/jianying/campaign/v1/pippit/share/landing_page"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": self.USER_AGENT,
        }
        self.data = {
            "title": "",
            "desc": None,
            "video_url": None,
            "video_list": [],
            "cover_url": None,
            "author": None,
            "image_list": [],
        }
        self._parse_once()

    def _parse_once(self):
        try:
            url = self.real_url
            parsed = urlparse(url)
            qdict = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if not qdict and "/s/" in parsed.path:
                target_fetch_url = url if url.endswith('/') else f"{url}/"
                response = self.session.get(
                    target_fetch_url,
                    headers={"User-Agent": self.USER_AGENT},
                    allow_redirects=True,
                    timeout=15,
                )
                response.raise_for_status()
                parsed = urlparse(response.url)
                qdict = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if not qdict:
                logger.warning(f"Unable to extract Xiaoyunque query parameters from URL: {self.real_url}")
                return

            # 公开免登录 H5 landing_page 接口
            response = self.session.post(
                self.API_URL,
                headers=self.headers,
                json={"query_params": qdict},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("err_no") != 0:
                raise ValueError(payload.get("err_tips") or "小云雀接口返回解析失败")

            self.data.update(self._format_data(payload.get("data") or {}))
        except Exception as exc:
            logger.exception(f"Failed to parse Xiaoyunque share: {exc}")

    @classmethod
    def _format_data(cls, data):
        page_info = data.get("page_info") or {}
        
        # 寻找命中的有效页面节点（兼容 generate_page, inspiration_page, template_page, gugu_page 等）
        target_page = {}
        for key in ("generate_page", "inspiration_page", "template_page", "share_page", "gugu_page"):
            if isinstance(page_info.get(key), dict):
                target_page = page_info[key]
                break
        if not target_page:
            for val in page_info.values():
                if isinstance(val, dict) and ("item_info" in val or "user_info" in val or "item_list" in val):
                    target_page = val
                    break

        item_list = target_page.get("item_list")
        if isinstance(item_list, list) and item_list:
            curr_idx = target_page.get("current_index")
            if not isinstance(curr_idx, int) or curr_idx < 0 or curr_idx >= len(item_list):
                curr_idx = 0
            item_info = item_list[curr_idx]
            user_info = item_info.get("author_info") or item_info.get("user_info") or target_page.get("user_info") or {}
        else:
            user_info = target_page.get("user_info") or target_page.get("author_info") or {}
            item_info = target_page.get("item_info") or {}

        title = item_info.get("title") or None
        desc = item_info.get("desc") or None

        # 图片列表
        image_info = item_info.get("image_info") or item_info.get("images") or []
        image_list = []
        if isinstance(image_info, list):
            for img in image_info:
                if isinstance(img, dict) and (img.get("image_url") or img.get("url")):
                    image_list.append(img.get("image_url") or img.get("url"))
                elif isinstance(img, str):
                    image_list.append(img)
        elif isinstance(image_info, dict):
            img_url = image_info.get("image_url") or image_info.get("url")
            if img_url:
                image_list.append(img_url)

        # 视频列表与主视频
        video_list = []
        raw_video_url = item_info.get("video_url") or item_info.get("video_play_url")
        video_info = item_info.get("video_info") or item_info.get("video") or []
        
        if isinstance(video_info, list):
            for v in video_info:
                if isinstance(v, dict):
                    v_url = v.get("video_url") or v.get("main_url") or v.get("url")
                    if v_url and v_url not in video_list:
                        video_list.append(v_url)
                elif isinstance(v, str) and v not in video_list:
                    video_list.append(v)
        elif isinstance(video_info, dict):
            v_url = video_info.get("main_url") or video_info.get("video_url") or video_info.get("url")
            if v_url and v_url not in video_list:
                video_list.append(v_url)

        if raw_video_url and raw_video_url not in video_list:
            video_list.insert(0, raw_video_url)

        primary_video_url = video_list[0] if video_list else None

        # 封面图
        cover_url = item_info.get("cover_url") or item_info.get("poster") or item_info.get("cover")
        if not cover_url and isinstance(video_info, list):
            for v in video_info:
                if isinstance(v, dict) and (v.get("cover_url") or v.get("poster") or v.get("cover")):
                    cover_url = v.get("cover_url") or v.get("poster") or v.get("cover")
                    break
        elif not cover_url and isinstance(video_info, dict):
            cover_url = video_info.get("cover_url") or video_info.get("poster") or video_info.get("cover")

        author = {
            "nickname": user_info.get("nick_name") or user_info.get("nickname") or "",
            "author_id": str(user_info.get("user_id") or user_info.get("sec_uid") or ""),
            "avatar": user_info.get("avatar_url") or user_info.get("avatar") or "",
        }

        return {
            "title": title,
            "desc": desc,
            "video_url": primary_video_url,
            "video_list": video_list,
            "cover_url": cover_url,
            "author": author,
            "image_list": image_list,
        }

    def get_real_video_url(self):
        return self.data.get("video_url")

    def get_video_list(self):
        return self.data.get("video_list") or []

    def get_title_content(self):
        return self.data.get("title") or None

    def get_description(self):
        return self.data.get("desc") or None

    def get_cover_photo_url(self):
        return self.data.get("cover_url")

    def get_author_info(self):
        return self.data.get("author")

    def get_image_list(self):
        return self.data.get("image_list") or []

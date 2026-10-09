from src.parser_factory import register_parser
import re
import json
import random
from urllib.parse import unquote
from src.parsers.base_parser import BaseParser
from configs.general_constants import USER_AGENT_M
from configs.logging_config import get_logger
logger = get_logger(__name__)


@register_parser("好看视频")
class HaokanParser(BaseParser):
    def __init__(self, real_url):
        super().__init__(real_url)
        self.headers = {
            "content-type": "application/json; charset=UTF-8",
            'User-Agent': random.choice(USER_AGENT_M),
            'referer': 'https://haokan.baidu.com/v'
        }
        self.data = self.fetch_html_data()

    def fetch_html_data(self):
        try:
            resp = self.session.get(self.real_url, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                self.html_content = resp.content.decode('utf-8', errors='replace')
            else:
                self.html_content = ""
        except Exception as e:
            logger.warning(f"Failed to fetch HTML content for {self.real_url}: {e}")
            self.html_content = ""

        if not self.html_content:
            return None

        # 1. 优先尝试提取现代百度/好看视频使用的 window.jsonData
        pattern_json_data = re.compile(r'window\.jsonData\s*=\s*(\{.*?\});\s*(?:window\.|\n|<)', re.DOTALL)
        match = pattern_json_data.search(self.html_content)
        if not match:
            pattern_json_data = re.compile(r'window\.jsonData\s*=\s*(\{.*)', re.DOTALL)
            match = pattern_json_data.search(self.html_content)
        if match:
            raw_json = match.group(1).strip()
            if raw_json.endswith(';'):
                raw_json = raw_json[:-1].strip()
            if '</script>' in raw_json:
                raw_json = raw_json.split('</script>')[0].strip().rstrip(';')
            try:
                data_dict = json.loads(raw_json)
                return json.dumps(data_dict)
            except Exception as e:
                logger.debug(f"Failed to decode window.jsonData: {e}")

        # 2. 兜底提取旧版 window.__PRELOADED_STATE__
        pattern_preloaded = re.compile(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*\};)', re.DOTALL)
        json_data = BaseParser.parse_html_data(self.html_content, pattern_preloaded)
        if json_data:
            return json_data

        # 3. 兜底提取百度移动端视频搜索落地页中的 script JSON 数据
        for s in re.findall(r'<script[^>]*>(.*?)</script>', self.html_content, re.DOTALL):
            st = s.strip()
            if st.startswith('{') and st.endswith('}') and ('"url"' in st or '"play_url"' in st or '"vid"' in st):
                try:
                    parsed_obj = json.loads(st)
                    if isinstance(parsed_obj, dict):
                        inner = parsed_obj.get('data') if isinstance(parsed_obj.get('data'), dict) else parsed_obj
                        if inner.get('url') or inner.get('play_url') or inner.get('videoInfo'):
                            return json.dumps(parsed_obj)
                except Exception:
                    pass

        return None

    def _get_data_dict(self):
        if not self.data:
            return {}
        if isinstance(self.data, dict):
            return self.data
        try:
            return json.loads(self.data)
        except Exception:
            return {}

    def get_real_video_url(self):
        try:
            data_dict = self._get_data_dict()
            if not data_dict:
                return None

            # 结构 A: window.jsonData (现代版本)
            inner_data = data_dict.get('data') if isinstance(data_dict.get('data'), dict) else data_dict
            video_info = inner_data.get('videoInfo') or inner_data.get('response') or {}
            if isinstance(video_info, dict):
                clarity_arr = video_info.get('clarityArr') or video_info.get('clarity_arr') or []
                if clarity_arr and isinstance(clarity_arr, list):
                    # 按 rank 降序排列选取最高画质流
                    sorted_clarity = sorted(
                        [item for item in clarity_arr if isinstance(item, dict) and item.get('url')],
                        key=lambda x: int(x.get('rank', 0)),
                        reverse=True
                    )
                    if sorted_clarity:
                        url = unquote(sorted_clarity[0]['url']).replace("\\/", "/")
                        return f"https:{url}" if url.startswith("//") else url
                play_url = video_info.get('play_url') or video_info.get('playurl') or video_info.get('url')
                if play_url:
                    url = unquote(play_url).replace("\\/", "/")
                    return f"https:{url}" if url.startswith("//") else url

            # 结构 B: window.__PRELOADED_STATE__ (旧版版本)
            cur_meta = data_dict.get('curVideoMeta') or inner_data.get('curVideoMeta') or {}
            clarity_url = cur_meta.get('clarityUrl', [])
            if clarity_url and isinstance(clarity_url, list):
                video_url = clarity_url[-1].get('url', '')
                if video_url:
                    url = unquote(video_url).replace("\\/", "/")
                    return f"https:{url}" if url.startswith("//") else url
            if cur_meta.get('playurl'):
                url = unquote(cur_meta['playurl']).replace("\\/", "/")
                return f"https:{url}" if url.startswith("//") else url

            # 结构 C: 搜索落地页直接包含的 url
            if inner_data.get('url') and isinstance(inner_data['url'], str) and ('.mp4' in inner_data['url'] or 'bdstatic' in inner_data['url']):
                url = unquote(inner_data['url']).replace("\\/", "/")
                return f"https:{url}" if url.startswith("//") else url

            return None
        except Exception as e:
            logger.warning(f"Failed to parse Haokan video URL: {e}")
            return None

    def get_title_content(self):
        try:
            data_dict = self._get_data_dict()
            if not data_dict:
                return ""

            inner_data = data_dict.get('data') if isinstance(data_dict.get('data'), dict) else data_dict
            video_info = inner_data.get('videoInfo') or inner_data.get('response') or {}
            if isinstance(video_info, dict) and video_info.get('title'):
                return video_info['title']
            if inner_data.get('title'):
                return inner_data['title']

            cur_meta = data_dict.get('curVideoMeta') or inner_data.get('curVideoMeta') or {}
            return cur_meta.get('title', '')
        except Exception as e:
            logger.warning(f"Failed to parse Haokan title: {e}")
            return ""

    def get_cover_photo_url(self):
        try:
            data_dict = self._get_data_dict()
            if not data_dict:
                return ""

            inner_data = data_dict.get('data') if isinstance(data_dict.get('data'), dict) else data_dict
            video_info = inner_data.get('videoInfo') or inner_data.get('response') or {}
            if isinstance(video_info, dict):
                cover = video_info.get('posterImage') or video_info.get('poster') or video_info.get('cover')
                if cover:
                    return cover.replace("\\/", "/")
            if inner_data.get('poster'):
                return inner_data['poster'].replace("\\/", "/")

            cur_meta = data_dict.get('curVideoMeta') or inner_data.get('curVideoMeta') or {}
            cover_url = cur_meta.get('poster', '')
            return cover_url.replace("\\/", "/") if cover_url else ""
        except Exception as e:
            logger.warning(f"Failed to parse Haokan cover URL: {e}")
            return ""

    def get_author_info(self):
        try:
            data_dict = self._get_data_dict()
            if not data_dict:
                return {}

            inner_data = data_dict.get('data') if isinstance(data_dict.get('data'), dict) else data_dict
            author_node = inner_data.get('author') or {}
            if isinstance(author_node, dict) and (author_node.get('name') or author_node.get('author_name')):
                return {
                    'nickname': author_node.get('name') or author_node.get('author_name', ''),
                    'author_id': str(author_node.get('uk') or author_node.get('mthid') or author_node.get('id', '')),
                    'avatar': (author_node.get('icon') or author_node.get('author_photo') or author_node.get('avatar', '')).replace('\\/', '/')
                }

            cur_meta = data_dict.get('curVideoMeta') or inner_data.get('curVideoMeta') or {}
            mth_node = cur_meta.get('mth', {})
            if isinstance(mth_node, dict):
                return {
                    'nickname': mth_node.get('author_name', ''),
                    'author_id': str(mth_node.get('mthid', '')),
                    'avatar': mth_node.get('author_photo', '').replace('\\/', '/')
                }
            return {}
        except Exception as e:
            logger.warning(f"Failed to parse author info: {e}")
            return {}

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from lawref.fetch.cache import DownloadCache
from lawref.fetch.models import LawItem
from lawref.parse import parse_file
from lawref.parse.base import ParsedDocument


class Provider(ABC):
    """数据源：下载原文，给出写进文件的标题和头部。列表的检索条件各数据源不同，见各自的方法。"""

    def __init__(self, cache: DownloadCache | None = None) -> None:
        self.cache = cache or DownloadCache()

    @abstractmethod
    async def fetch_document(self, item: LawItem) -> Path:
        """下载原文到缓存，返回文件路径。"""

    async def note(self, item: LawItem) -> str:
        """原文里没有题注时，由数据源给出的头部信息，格式同「（2020年…公布　自…起施行）」。"""
        return ""

    async def header_note(self, item: LawItem, parsed_note: str) -> str:
        """写进头部的信息：原文里解析出来的优先，没有时用数据源给的。"""
        return parsed_note or await self.note(item)

    def heading(self, item: LawItem) -> str:
        """文件里的 # 标题。"""
        return item.full_title or item.title

    async def parse_document(self, path: Path, title: str) -> ParsedDocument:
        """解析原文。一页里有多个案例的数据源覆写它，按 title 取对应的那一个。"""
        # 解析 Word、转换 .doc 会占住事件循环，放到线程里
        return await asyncio.to_thread(parse_file, path, title)

"""併發上限。

分開重路徑與輕路徑：單一上傳的向量化若不設限會吃光 CPU，
使得查詢連一句話的向量化都排不進去。模型呼叫是網路等待，額度另計。
"""
import asyncio

from app.config import settings

INDEX = asyncio.Semaphore(settings.index_concurrency)    # 解析 + 向量化，CPU-bound
QUERY = asyncio.Semaphore(settings.query_concurrency)    # 單句查詢向量化

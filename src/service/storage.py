"""Thread-safe Repository & Cache-Aside Storage for URL records and click analytics."""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ClickEvent(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    referrer: str = "direct"
    user_agent: str = "unknown"


class ShortUrlRecord(BaseModel):
    code: str
    target_url: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None
    revoked: bool = False
    is_custom_alias: bool = False
    click_count: int = 0
    recent_clicks: List[ClickEvent] = Field(default_factory=list)
    referrer_breakdown: Dict[str, int] = Field(default_factory=dict)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        ref = now or datetime.now(timezone.utc)
        exp = datetime.fromisoformat(self.expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return ref >= exp


class UrlRepository:
    """Thread-safe repository with an LRU hot-cache layer."""

    def __init__(self, cache_size: int = 1024):
        self._records: Dict[str, ShortUrlRecord] = {}
        self._cache: OrderedDict[str, ShortUrlRecord] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.RLock()
        self.cache_hits = 0
        self.cache_misses = 0

    def _put_cache(self, record: ShortUrlRecord) -> None:
        if record.code in self._cache:
            self._cache.move_to_end(record.code)
        self._cache[record.code] = record
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def exists(self, code: str) -> bool:
        with self._lock:
            return code in self._records

    def save(self, record: ShortUrlRecord) -> ShortUrlRecord:
        with self._lock:
            self._records[record.code] = record
            self._put_cache(record)
            return record

    def get(self, code: str) -> Optional[ShortUrlRecord]:
        with self._lock:
            if code in self._cache:
                self.cache_hits += 1
                self._cache.move_to_end(code)
                return self._cache[code]
            self.cache_misses += 1
            rec = self._records.get(code)
            if rec is not None:
                self._put_cache(rec)
            return rec

    def record_click(self, code: str, referrer: str, user_agent: str) -> None:
        with self._lock:
            rec = self._records.get(code)
            if not rec:
                return
            rec.click_count += 1
            ref_key = referrer or "direct"
            rec.referrer_breakdown[ref_key] = rec.referrer_breakdown.get(ref_key, 0) + 1
            rec.recent_clicks.append(ClickEvent(referrer=ref_key, user_agent=user_agent or "unknown"))
            if len(rec.recent_clicks) > 50:
                rec.recent_clicks.pop(0)
            self._put_cache(rec)

    def revoke(self, code: str) -> bool:
        with self._lock:
            rec = self._records.get(code)
            if not rec:
                return False
            rec.revoked = True
            self._cache.pop(code, None)
            return True

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total_urls": len(self._records),
                "cached_urls": len(self._cache),
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
            }

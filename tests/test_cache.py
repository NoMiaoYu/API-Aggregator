"""DataCache 单元测试。"""

from app.core.cache import CacheEntry, DataCache


def test_set_and_get_returns_entry():
    cache = DataCache()
    entry = CacheEntry(data={"v": 1}, md5="abc", timestamp=100, source="foo")
    cache.set("/api/foo", entry)
    assert cache.get("/api/foo") is entry


def test_get_missing_key_returns_none():
    cache = DataCache()
    assert cache.get("/api/missing") is None


def test_set_overwrites_existing_entry():
    cache = DataCache()
    old = CacheEntry(data={"v": 1}, md5="a", timestamp=1, source="s")
    new = CacheEntry(data={"v": 2}, md5="b", timestamp=2, source="s")
    cache.set("/api/foo", old)
    cache.set("/api/foo", new)
    assert cache.get("/api/foo") is new


def test_keys_lists_all_stored_keys():
    cache = DataCache()
    cache.set("/api/a", CacheEntry({}, "1", 1, "x"))
    cache.set("/api/b", CacheEntry({}, "2", 2, "x"))
    assert sorted(cache.keys()) == ["/api/a", "/api/b"]


def test_keys_empty_when_no_entries():
    assert DataCache().keys() == []


def test_snapshot_returns_shallow_copy():
    cache = DataCache()
    cache.set("/api/a", CacheEntry({"v": 1}, "1", 1, "x"))
    snap = cache.snapshot()
    assert set(snap.keys()) == {"/api/a"}
    # 修改 snapshot 不影响原 cache
    snap.pop("/api/a")
    assert "/api/a" in cache.keys()
    # 修改 entry.data 会被互相影响（浅拷贝）
    snap2 = cache.snapshot()
    snap2["/api/a"].data["v"] = 99
    assert cache.get("/api/a").data["v"] == 99


def test_remove_deletes_key():
    cache = DataCache()
    cache.set("/api/a", CacheEntry({}, "1", 1, "x"))
    cache.remove("/api/a")
    assert cache.get("/api/a") is None


def test_remove_missing_key_is_noop():
    cache = DataCache()
    cache.remove("/api/none")  # 不抛异常
    assert cache.keys() == []


def test_clear_empties_cache():
    cache = DataCache()
    cache.set("/api/a", CacheEntry({}, "1", 1, "x"))
    cache.set("/api/b", CacheEntry({}, "2", 2, "x"))
    cache.clear()
    assert cache.keys() == []
    assert cache.get("/api/a") is None


def test_cache_entry_default_construction():
    entry = CacheEntry(data="payload", md5="m", timestamp=0, source="src")
    assert entry.data == "payload"
    assert entry.md5 == "m"
    assert entry.timestamp == 0
    assert entry.source == "src"

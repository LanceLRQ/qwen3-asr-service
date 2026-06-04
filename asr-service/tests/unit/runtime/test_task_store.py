"""app/runtime/task_store.py 测试（P1）：建库/CRUD/启动收口/TTL 清理/节流/容错。"""
from datetime import datetime, timedelta

import pytest

import app.runtime.task_store as ts
from app.runtime.task_store import TaskStore


@pytest.fixture
def store_factory(tmp_path):
    """创建 TaskStore 的工厂，测试结束统一 close，避免连接泄漏。"""
    created = []

    def _make(name="tasks.db", retention_days=7):
        s = TaskStore(str(tmp_path / name), retention_days=retention_days)
        created.append(s)
        return s

    yield _make

    for s in created:
        s.close()


def _task(task_id="t1", status="pending", **kw):
    """模拟 TaskManager 的 task dict（仅持久化关心的键）。"""
    base = {
        "task_id": task_id,
        "status": status,
        "progress": 0.0,
        "language": "zh",
        "wav_name": "a.wav",
        "result": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
        "finished_at": None,
    }
    base.update(kw)
    return base


# ─── 建库 / 重开 ───

def test_create_db_and_schema_version(store_factory, tmp_path):
    store = store_factory()
    assert (tmp_path / "tasks.db").exists()
    row = store._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert row["value"] == str(TaskStore.SCHEMA_VERSION)


def test_reopen_existing_db_keeps_data(store_factory, tmp_path):
    store = store_factory()
    store.insert_task(_task("t1"))
    store.close()
    store2 = TaskStore(str(tmp_path / "tasks.db"))
    try:
        assert store2.get_task("t1")["task_id"] == "t1"
    finally:
        store2.close()


# ─── insert / get 往返 ───

def test_insert_and_get_roundtrip(store_factory):
    store = store_factory()
    store.insert_task(_task("t1"))
    task = store.get_task("t1")
    assert task["status"] == "pending"
    assert task["language"] == "zh"
    assert task["wav_name"] == "a.wav"
    assert task["result"] is None


def test_get_missing_returns_none(store_factory):
    assert store_factory().get_task("nope") is None


def test_insert_duplicate_keeps_existing(store_factory, caplog):
    """task_id 冲突时不覆盖既有记录（INSERT OR IGNORE），仅告警。"""
    store = store_factory()
    t = _task("t1")
    store.insert_task(t)
    t.update(status="failed", error="boom", finished_at=datetime.now().isoformat())
    store.finalize_task(t)

    with caplog.at_level("WARNING"):
        store.insert_task(_task("t1"))  # 重复插入 pending
    task = store.get_task("t1")
    assert task["status"] == "failed"           # 原终态记录未被覆盖
    assert task["error"] == "boom"
    assert "已存在" in caplog.text


def test_update_status_missing_task_warns(store_factory, caplog):
    """UPDATE 匹配 0 行（如 insert 曾失败）不再静默，记录告警。"""
    store = store_factory()
    with caplog.at_level("WARNING"):
        store.update_status("ghost", "processing")
    assert "无匹配记录" in caplog.text


def test_finalize_writes_result_json(store_factory):
    store = store_factory()
    t = _task("t1")
    store.insert_task(t)
    t.update(status="completed", progress=1.0,
             result={"full_text": "你好", "segments": [{"text": "你好"}]},
             finished_at=datetime.now().isoformat())
    store.finalize_task(t)
    task = store.get_task("t1")
    assert task["status"] == "completed"
    assert task["result"] == {"full_text": "你好", "segments": [{"text": "你好"}]}
    assert task["finished_at"] is not None


def test_finalize_unserializable_result_keeps_metadata(store_factory):
    store = store_factory()
    t = _task("t1")
    store.insert_task(t)
    t.update(status="completed", result={"bad": object()},
             finished_at=datetime.now().isoformat())
    store.finalize_task(t)  # 序列化失败不抛错
    task = store.get_task("t1")
    assert task["status"] == "completed"
    assert task["result"] is None


# ─── 状态跃迁 / 进度节流 ───

def test_update_status_immediate(store_factory):
    store = store_factory()
    store.insert_task(_task("t1"))
    store.update_status("t1", "processing")
    assert store.get_task("t1")["status"] == "processing"


def test_save_progress_throttled(store_factory, monkeypatch):
    store = store_factory()
    store.insert_task(_task("t1"))

    fake_now = [100.0]
    monkeypatch.setattr(ts.time, "monotonic", lambda: fake_now[0])

    store.save_progress("t1", 0.1)                      # 首次写入
    assert store.get_task("t1")["progress"] == 0.1
    store.save_progress("t1", 0.2)                      # <1s，跳过
    assert store.get_task("t1")["progress"] == 0.1
    fake_now[0] += 1.5
    store.save_progress("t1", 0.3)                      # 超过间隔，落库
    assert store.get_task("t1")["progress"] == 0.3


def test_finalize_resets_progress_throttle(store_factory, monkeypatch):
    """终态清掉节流记录，避免 task_id 字典随历史任务无限增长。"""
    store = store_factory()
    store.insert_task(_task("t1"))
    store.save_progress("t1", 0.5)
    t = _task("t1", status="completed", finished_at=datetime.now().isoformat())
    store.finalize_task(t)
    assert "t1" not in store._last_progress_write


# ─── list_history / delete ───

def test_list_history_only_terminal_desc(store_factory):
    store = store_factory()
    base = datetime(2026, 6, 1, 10, 0, 0)
    for i, status in enumerate(["completed", "failed", "pending"]):
        t = _task(f"t{i}", created_at=(base + timedelta(minutes=i)).isoformat())
        store.insert_task(t)
        if status != "pending":
            t.update(status=status, finished_at=(base + timedelta(minutes=i + 1)).isoformat())
            store.finalize_task(t)
    history = store.list_history()
    assert [t["task_id"] for t in history] == ["t1", "t0"]   # 终态倒序，pending 不出现
    assert all("result" not in t for t in history)           # 摘要不含 result


def test_list_history_limit(store_factory):
    store = store_factory()
    base = datetime(2026, 6, 1, 10, 0, 0)
    for i in range(5):
        t = _task(f"t{i}", created_at=(base + timedelta(minutes=i)).isoformat())
        store.insert_task(t)
        t.update(status="completed", finished_at=(base + timedelta(minutes=i + 1)).isoformat())
        store.finalize_task(t)
    assert len(store.list_history(limit=3)) == 3


def test_list_history_status_filter_in_sql(store_factory):
    """status 过滤在 SQL 侧执行，limit 语义按过滤后结果计算。"""
    store = store_factory()
    base = datetime(2026, 6, 1, 10, 0, 0)
    for i in range(4):
        status = "failed" if i % 2 else "completed"
        t = _task(f"t{i}", created_at=(base + timedelta(minutes=i)).isoformat())
        store.insert_task(t)
        t.update(status=status, finished_at=(base + timedelta(minutes=i + 1)).isoformat())
        store.finalize_task(t)
    failed = store.list_history(limit=10, status="failed")
    assert [t["task_id"] for t in failed] == ["t3", "t1"]
    assert store.list_history(limit=1, status="failed")[0]["task_id"] == "t3"


def test_delete_task(store_factory):
    store = store_factory()
    store.insert_task(_task("t1"))
    assert store.delete_task("t1") is True
    assert store.get_task("t1") is None
    assert store.delete_task("t1") is False


# ─── 启动时序：悬挂收口 / TTL 清理 ───

def test_close_dangling_marks_failed(store_factory):
    store = store_factory()
    store.insert_task(_task("t1", status="pending"))
    t2 = _task("t2", status="processing")
    store.insert_task(t2)
    store.update_status("t2", "processing")
    t3 = _task("t3")
    store.insert_task(t3)
    t3.update(status="completed", finished_at=datetime.now().isoformat())
    store.finalize_task(t3)

    assert store.close_dangling() == 2
    for tid in ("t1", "t2"):
        task = store.get_task(tid)
        assert task["status"] == "failed"
        assert task["error"] == "service restarted"
        assert task["finished_at"] is not None
    assert store.get_task("t3")["status"] == "completed"     # 终态不受影响


def test_cleanup_expired_boundary(store_factory):
    store = store_factory(retention_days=7)
    old = _task("old", created_at=(datetime.now() - timedelta(days=9)).isoformat())
    store.insert_task(old)
    old.update(status="completed", finished_at=(datetime.now() - timedelta(days=8)).isoformat())
    store.finalize_task(old)
    fresh = _task("fresh")
    store.insert_task(fresh)
    fresh.update(status="completed", finished_at=datetime.now().isoformat())
    store.finalize_task(fresh)

    assert store.cleanup_expired() == 1
    assert store.get_task("old") is None
    assert store.get_task("fresh") is not None


def test_cleanup_zero_retention_never_deletes(store_factory):
    store = store_factory(retention_days=0)
    t = _task("t1", created_at=(datetime.now() - timedelta(days=365)).isoformat())
    store.insert_task(t)
    t.update(status="completed", finished_at=(datetime.now() - timedelta(days=364)).isoformat())
    store.finalize_task(t)
    assert store.cleanup_expired() == 0
    assert store.get_task("t1") is not None


def test_cleanup_keeps_unfinished(store_factory):
    """未终态记录（finished_at IS NULL）永不被 TTL 清理（由 close_dangling 收口）。"""
    store = store_factory(retention_days=7)
    store.insert_task(_task("t1", created_at=(datetime.now() - timedelta(days=30)).isoformat()))
    assert store.cleanup_expired() == 0
    assert store.get_task("t1") is not None


# ─── 容错：库异常绝不向主链路抛错 ───

def test_all_methods_degrade_after_close(store_factory):
    store = store_factory()
    t = _task("t1")
    store.insert_task(t)
    store.close()
    # close 后底层连接抛 ProgrammingError(sqlite3.Error 子类)，全部静默降级
    store.insert_task(_task("t2"))
    store.update_status("t1", "processing")
    store.save_progress("t1", 0.5)
    store.finalize_task(_task("t1", status="completed"))
    assert store.get_task("t1") is None
    assert store.list_history() == []
    assert store.delete_task("t1") is False
    assert store.close_dangling() == 0
    assert store.cleanup_expired() == 0
    store.close()  # 二次 close 幂等

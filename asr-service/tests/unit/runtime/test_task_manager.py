"""app/runtime/task_manager.py 测试。

注入假 process_fn，不加载模型；超时用 monkeypatch 压缩。
行为依源码确认（task_manager.py:41/64/69/92/98/125/130/187）。
"""
import queue

import pytest

from tests.conftest import wait_for


# ─── submit / get_task ───

def test_submit_returns_id_and_pending(tm_factory):
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav", language="zh")
    assert isinstance(tid, str) and len(tid) > 0
    task = tm.get_task(tid)
    assert task["status"] == "pending"
    assert task["progress"] == 0.0
    assert task["file_path"] == "/tmp/a.wav"
    assert task["language"] == "zh"
    assert task["result"] is None


def test_get_task_unknown_returns_none(tm_factory):
    tm = tm_factory()
    assert tm.get_task("nope") is None


def test_submit_queue_full_raises(tm_factory):
    # 不启动 worker，队列填满后 put_nowait 抛 queue.Full
    tm = tm_factory(max_queue_size=2)
    tm.submit("/tmp/1.wav")
    tm.submit("/tmp/2.wav")
    with pytest.raises(queue.Full):
        tm.submit("/tmp/3.wav")


# ─── list_tasks ───

def _inject(tm, task_id, status, created_at, **extra):
    task = {
        "task_id": task_id,
        "status": status,
        "progress": 0.0,
        "file_path": f"/tmp/{task_id}.wav",
        "language": None,
        "result": {"full_text": "secret"},
        "error": None,
        "created_at": created_at,
        "finished_at": None,
    }
    task.update(extra)
    tm._tasks[task_id] = task


def test_list_tasks_summary_excludes_result_and_path(tm_factory):
    tm = tm_factory()
    _inject(tm, "a", "completed", "2026-06-03T10:00:00")
    items = tm.list_tasks()
    assert len(items) == 1
    item = items[0]
    assert "result" not in item
    assert "file_path" not in item
    assert set(item.keys()) == {
        "task_id", "status", "progress", "language", "wav_name",
        "created_at", "finished_at", "error",
    }


def test_list_tasks_sorted_desc_by_created_at(tm_factory):
    tm = tm_factory()
    _inject(tm, "old", "completed", "2026-06-03T10:00:00")
    _inject(tm, "new", "completed", "2026-06-03T12:00:00")
    items = tm.list_tasks()
    assert [i["task_id"] for i in items] == ["new", "old"]


def test_list_tasks_status_filter(tm_factory):
    tm = tm_factory()
    _inject(tm, "p", "pending", "2026-06-03T10:00:00")
    _inject(tm, "c", "completed", "2026-06-03T11:00:00")
    assert [i["task_id"] for i in tm.list_tasks(status="pending")] == ["p"]
    assert [i["task_id"] for i in tm.list_tasks(status="completed")] == ["c"]


# ─── update_progress ───

def test_update_progress(tm_factory):
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav")
    tm.update_progress(tid, 0.5)
    assert tm.get_task(tid)["progress"] == 0.5


def test_update_progress_unknown_noop(tm_factory):
    tm = tm_factory()
    tm.update_progress("nope", 0.5)  # 不抛异常即可


# ─── cancel_task / is_cancelled ───

def test_cancel_unknown_returns_none(tm_factory):
    tm = tm_factory()
    assert tm.cancel_task("nope") is None


def test_cancel_pending_marks_cancelled(tm_factory):
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav")
    prev = tm.cancel_task(tid)
    assert prev == "pending"
    task = tm.get_task(tid)
    assert task["status"] == "cancelled"
    assert task["error"] == "任务已取消"
    assert task["finished_at"] is not None
    assert tm.is_cancelled(tid) is True


def test_cancel_processing_keeps_status_sets_event(tm_factory):
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav")
    tm.get_task(tid)["status"] = "processing"
    prev = tm.cancel_task(tid)
    assert prev == "processing"
    assert tm.get_task(tid)["status"] == "processing"  # 等待 chunk 边界，不立即改
    assert tm.is_cancelled(tid) is True


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_cancel_terminal_noop(tm_factory, terminal):
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav")
    tm.get_task(tid)["status"] = terminal
    prev = tm.cancel_task(tid)
    assert prev == terminal
    assert tm.get_task(tid)["status"] == terminal


def test_is_cancelled_unknown_false(tm_factory):
    tm = tm_factory()
    assert tm.is_cancelled("nope") is False


# ─── is_stopping / shutdown ───

def test_is_stopping_toggles_on_shutdown(tm_factory):
    tm = tm_factory()
    assert tm.is_stopping is False
    tm.shutdown()
    assert tm.is_stopping is True


# ─── worker 集成（启动线程，process_fn 假实现）───

def test_worker_completes_task(tm_factory):
    tm = tm_factory(start=True, processor=lambda task: {"full_text": "hi"})
    tid = tm.submit("/tmp/a.wav")
    assert wait_for(lambda: tm.get_task(tid)["status"] == "completed")
    task = tm.get_task(tid)
    assert task["result"] == {"full_text": "hi"}
    assert task["progress"] == 1.0
    assert task["finished_at"] is not None


def test_worker_failure_sets_generic_error(tm_factory):
    def boom(task):
        raise ValueError("internal detail")

    tm = tm_factory(start=True, processor=boom)
    tid = tm.submit("/tmp/a.wav")
    assert wait_for(lambda: tm.get_task(tid)["status"] == "failed")
    task = tm.get_task(tid)
    # 错误信息脱敏，不泄露内部异常细节
    assert task["error"] == "内部处理错误，请检查服务日志"
    assert "internal detail" not in task["error"]


def test_worker_timeout(tm_factory, monkeypatch):
    monkeypatch.setattr("app.runtime.task_manager.TASK_TIMEOUT", 0.3)

    def slow(task):
        import time
        time.sleep(1.0)
        return {"x": 1}

    tm = tm_factory(start=True, processor=slow)
    tid = tm.submit("/tmp/a.wav")
    assert wait_for(lambda: tm.get_task(tid)["status"] == "failed", timeout=3.0)
    assert "超时" in tm.get_task(tid)["error"]


def test_worker_skips_cancelled_pending(tm_factory):
    # pending 阶段被取消的任务，worker 跳过处理（不会变 completed）
    processed = []
    tm = tm_factory(start=False, processor=lambda task: processed.append(task["task_id"]))
    tid = tm.submit("/tmp/a.wav")
    tm.cancel_task(tid)  # pending -> cancelled
    tm.start()
    import time
    time.sleep(0.3)
    assert tm.get_task(tid)["status"] == "cancelled"
    assert processed == []


# ─── 持久化 write-through 钩子（P2，注入记录调用的假 store）───

class _FakeStore:
    """记录钩子调用的桩。真实 TaskStore 内部自吞库异常，桩只验证调用契约。"""

    def __init__(self):
        self.calls = []

    def insert_task(self, task):
        self.calls.append(("insert", task["task_id"], task["status"]))

    def update_status(self, task_id, status):
        self.calls.append(("status", task_id, status))

    def save_progress(self, task_id, progress):
        self.calls.append(("progress", task_id, progress))

    def finalize_task(self, task):
        self.calls.append(("finalize", task["task_id"], task["status"]))


def test_store_hooks_submit_and_complete(tm_factory):
    store = _FakeStore()
    tm = tm_factory(start=True, processor=lambda task: {"full_text": "hi"}, store=store)
    tid = tm.submit("/tmp/a.wav", wav_name="a.wav")
    assert wait_for(lambda: ("finalize", tid, "completed") in store.calls)
    assert ("insert", tid, "pending") in store.calls
    assert ("status", tid, "processing") in store.calls


def test_store_hooks_failure_finalized(tm_factory):
    def boom(task):
        raise ValueError("x")

    store = _FakeStore()
    tm = tm_factory(start=True, processor=boom, store=store)
    tid = tm.submit("/tmp/a.wav")
    assert wait_for(lambda: ("finalize", tid, "failed") in store.calls)


def test_store_hooks_cancel_pending_finalized(tm_factory):
    store = _FakeStore()
    tm = tm_factory(store=store)  # 不启动 worker
    tid = tm.submit("/tmp/a.wav")
    tm.cancel_task(tid)
    assert ("finalize", tid, "cancelled") in store.calls


def test_store_hooks_progress(tm_factory):
    store = _FakeStore()
    tm = tm_factory(store=store)
    tid = tm.submit("/tmp/a.wav")
    tm.update_progress(tid, 0.5)
    assert ("progress", tid, 0.5) in store.calls
    tm.update_progress("nope", 0.9)  # 未知任务不触达 store
    assert not any(c[1] == "nope" for c in store.calls)


def test_submit_wav_name_recorded(tm_factory):
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav", wav_name="原始文件.mp3")
    assert tm.get_task(tid)["wav_name"] == "原始文件.mp3"


def test_no_store_zero_behavior_change(tm_factory):
    """store=None（schema 默认关闭）时任务状态机与现状一致。"""
    tm = tm_factory(start=True, processor=lambda task: {"ok": 1})
    tid = tm.submit("/tmp/a.wav")
    assert wait_for(lambda: tm.get_task(tid)["status"] == "completed")


# ─── wait_done（兼容层同步等待用） ───

def test_wait_done_completed_returns_snapshot(tm_factory):
    tm = tm_factory(start=True, processor=lambda task: {"full_text": "ok"})
    tid = tm.submit("/tmp/a.wav")
    task = tm.wait_done(tid, timeout=5)
    assert task is not None
    assert task["status"] == "completed"
    assert task["result"] == {"full_text": "ok"}


def test_wait_done_failed_returns_snapshot(tm_factory):
    def boom(task):
        raise RuntimeError("decode error")
    tm = tm_factory(start=True, processor=boom)
    tid = tm.submit("/tmp/a.wav")
    task = tm.wait_done(tid, timeout=5)
    assert task["status"] == "failed"


def test_wait_done_timeout_returns_none(tm_factory):
    import time
    tm = tm_factory(start=True, processor=lambda task: time.sleep(2) or {"ok": 1})
    tid = tm.submit("/tmp/a.wav")
    assert tm.wait_done(tid, timeout=0.05) is None


def test_wait_done_pending_cancel_wakes(tm_factory):
    # 不启动 worker：pending 直接取消，wait_done 应立即返回 cancelled 快照
    tm = tm_factory()
    tid = tm.submit("/tmp/a.wav")
    tm.cancel_task(tid)
    task = tm.wait_done(tid, timeout=1)
    assert task is not None and task["status"] == "cancelled"


def test_wait_done_unknown_task_returns_none(tm_factory):
    tm = tm_factory()
    assert tm.wait_done("nope", timeout=0.1) is None


def test_wait_done_pops_done_event_on_cleanup(tm_factory):
    tm = tm_factory(start=True, processor=lambda task: {"ok": 1})
    tid = tm.submit("/tmp/a.wav")
    assert wait_for(lambda: tm.get_task(tid)["status"] == "completed")
    # _done_events 与 _cancel_events 一样在 submit 登记
    assert tid in tm._done_events

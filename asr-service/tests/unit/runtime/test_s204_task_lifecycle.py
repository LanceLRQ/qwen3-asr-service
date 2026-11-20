import asyncio
import queue
from contextlib import closing
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import routes
from app.api.compat import openai_routes


@pytest.fixture
def loop():
    with closing(asyncio.new_event_loop()) as loop:
        yield loop


@pytest.mark.parametrize("module", [routes, openai_routes])
def test_interrupted_upload_removes_partial_file(loop, module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "UPLOADS_DIR", str(tmp_path))
    upload = Mock(filename="clip.wav", read=AsyncMock(side_effect=[b"audio", asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        loop.run_until_complete(module._save_upload(upload))
    assert list(tmp_path.iterdir()) == []


def test_rejected_submission_leaves_no_pending_task(tm_factory):
    manager = tm_factory(max_queue_size=1)
    accepted = manager.submit("first.wav")
    with pytest.raises(queue.Full):
        manager.submit("second.wav")
    assert [task["task_id"] for task in manager.list_tasks()] == [accepted]


@pytest.mark.parametrize("owned", [False, True])
def test_pending_cancel_respects_input_ownership(tm_factory, tmp_path, owned):
    path = tmp_path / "input.wav"
    path.touch()
    manager = tm_factory()
    task_id = manager.submit(str(path), owns_file=owned)
    manager.cancel_task(task_id)
    assert manager.wait_done(task_id, 0)["status"] == "cancelled"
    assert path.exists() != owned


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_inflight_completion_releases_owned_file(tm_factory, tmp_path, cancelled, failed):
    path = tmp_path / "upload.wav"
    path.touch()
    started, finish = Event(), Event()
    calls = []

    def process(task):
        calls.append(task["file_path"])
        started.set()
        assert finish.wait(2)
        assert path.exists()
        if failed:
            raise RuntimeError("private upstream detail")
        return {"full_text": "ok"}

    manager = tm_factory(start=True, processor=process)
    task_id = manager.submit(str(path), owns_file=True)
    assert started.wait(2)
    if cancelled:
        manager.cancel_task(task_id)
    finish.set()
    task = manager.wait_done(task_id, 2)
    assert task["status"] == ("cancelled" if cancelled else "failed" if failed else "completed")
    assert calls == [str(path)]
    assert not path.exists()


def test_dashscope_forwards_task_failure(loop, monkeypatch):
    from app.api.compat import dashscope_routes as ds

    rec = Mock(subtasks=[ds.SubTask(0, "https://example.org/input.wav", inner_id="child")])
    monkeypatch.setattr(ds, "_get_rec", lambda _: rec)
    manager = Mock()
    manager.get_task.return_value = {"status": "failed", "error": "上游鉴权失败"}
    monkeypatch.setattr(ds, "_task_manager", manager)
    result = loop.run_until_complete(ds.query_task("parent", Mock()))["output"]["results"][0]
    assert result["subtask_status"] == "FAILED"
    assert result["code"] == "InternalError"
    assert result["message"] == "上游鉴权失败"


def test_processor_timeout_is_not_worker_deadline(tm_factory):
    def process(task):
        raise TimeoutError("private transport details")

    manager = tm_factory(start=True, processor=process)
    task = manager.wait_done(manager.submit("clip.wav"), 2)
    assert task["status"] == "failed"
    assert task["error"] == "内部处理错误，请检查服务日志"
    assert not manager.is_cancelled(task["task_id"])


def test_shutdown_without_worker_releases_pending_file(tm_factory, tmp_path):
    manager = tm_factory()
    path = tmp_path / "upload.wav"
    path.touch()
    task_id = manager.submit(str(path), owns_file=True)
    assert manager.shutdown()
    assert not path.exists()
    assert manager.wait_done(task_id, 0)["status"] == "cancelled"


def test_shutdown_signals_running_task_and_releases_inputs(tm_factory, tmp_path):
    running = tmp_path / "running.wav"
    pending = tmp_path / "pending.wav"
    running.touch()
    pending.touch()
    started = Event()

    def process(task):
        from tests.conftest import wait_for
        started.set()
        assert wait_for(lambda: manager.is_cancelled(task["task_id"]), timeout=2)
        assert running.exists()
        return {}

    manager = tm_factory(start=True, processor=process)
    active = manager.submit(str(running), owns_file=True)
    assert started.wait(2)
    queued = manager.submit(str(pending), owns_file=True)
    assert manager.shutdown()
    for task_id in [active, queued]:
        assert manager.wait_done(task_id, 0)["status"] == "cancelled"
    assert list(tmp_path.iterdir()) == []


def test_cancelled_dashscope_download_removes_partial_file(loop, tmp_path, monkeypatch):
    from app.api.compat import fetch
    from unittest.mock import MagicMock

    async def chunks(size):
        yield b"partial"
        raise asyncio.CancelledError

    client = MagicMock()
    response = Mock(is_redirect=False, status_code=200, aiter_bytes=chunks)
    client.stream.return_value.__aenter__.return_value = response
    context = MagicMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(fetch.httpx, "AsyncClient", lambda **kwargs: context)
    monkeypatch.setattr(fetch, "UPLOADS_DIR", str(tmp_path))
    with pytest.raises(asyncio.CancelledError):
        loop.run_until_complete(fetch.fetch_to_local("http://127.0.0.1/a.wav", max_mb=1,
                                   timeout_s=1, allow_private=True))
    assert list(tmp_path.iterdir()) == []


def test_shutdown_cancels_executor_backlog_after_deadline(tm_factory, tmp_path, monkeypatch):
    from app.runtime import task_manager
    from tests.conftest import wait_for

    monkeypatch.setattr(task_manager, "TASK_TIMEOUT", 0.1)
    started, release = Event(), Event()
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    first.touch()
    second.touch()
    calls = []

    def process(task):
        calls.append(task["task_id"])
        started.set()
        release.wait(8)
        return {}

    manager = tm_factory(start=True, processor=process)
    active = manager.submit(str(first), owns_file=True)
    try:
        assert started.wait(2)
        assert manager.wait_done(active, 2)["status"] == "failed"
        assert manager.is_cancelled(active) and first.exists()
        monkeypatch.setattr(task_manager, "TASK_TIMEOUT", 60)
        queued = manager.submit(str(second), owns_file=True)
        assert wait_for(lambda: manager.get_task(queued)["status"] == "processing")
        assert manager.shutdown()
        assert manager.wait_done(queued, 0)["status"] == "cancelled"
        assert not second.exists()
    finally:
        release.set()
    assert wait_for(lambda: not first.exists())
    assert calls == [active]


def test_completed_task_keeps_baseline_timing_log(tm_factory, caplog):
    import logging
    import re

    store = Mock()
    with caplog.at_level(logging.INFO, logger="app.runtime.task_manager"):
        manager = tm_factory(start=True, processor=lambda task: {}, store=store)
        tid = manager.submit("clip.wav")
        task = manager.wait_done(tid, 2)
    assert task["status"] == "completed"
    store.finalize_task.assert_called_once_with(task)
    assert re.search(rf"任务完成: {tid} \(\d+\.\ds\)", caplog.text)


def test_store_insert_precedes_worker_finalization(tm_factory):
    finalized = Event()
    writes = []
    store = Mock()

    def insert(task):
        finalized.wait(0.2)
        writes.append(("insert", task["status"]))

    def finalize(task):
        writes.append(("finalize", task["status"]))
        finalized.set()

    store.insert_task.side_effect = insert
    store.finalize_task.side_effect = finalize
    manager = tm_factory(start=True, processor=lambda task: {}, store=store)
    task = manager.wait_done(manager.submit("clip.wav"), 2)
    assert task["status"] == "completed"
    assert writes == [("insert", "pending"), ("finalize", "completed")]

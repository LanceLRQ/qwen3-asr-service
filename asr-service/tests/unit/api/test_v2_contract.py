"""契约测试：/v1 零破坏 + /v2 同名别名一致 + health 字段向后兼容。

守护开发铁律 #5（不破坏 v1）。路由清单快照防止误删/改 v1 端点。
"""
from unittest.mock import MagicMock

from app.api import routes, common_routes


def _route_set(router):
    pairs = set()
    for r in router.routes:
        for m in getattr(r, "methods", set()) or set():
            if m in ("HEAD", "OPTIONS"):
                continue
            pairs.add((m, r.path))
    return pairs


# ─── 路由清单快照 ───

def test_v1_offline_route_inventory():
    got = _route_set(routes.build_offline_router("/v1", include_deprecated=True))
    assert got == {
        ("POST", "/v1/asr"),
        ("GET", "/v1/tasks"),
        ("GET", "/v1/tasks/{task_id}"),
        ("DELETE", "/v1/tasks/{task_id}"),
        ("GET", "/v1/asr/{task_id}"),       # deprecated 别名
    }


def test_v2_offline_route_inventory_excludes_deprecated():
    got = _route_set(routes.build_offline_router("/v2"))
    assert got == {
        ("POST", "/v2/asr"),
        ("GET", "/v2/tasks"),
        ("GET", "/v2/tasks/{task_id}"),
        ("DELETE", "/v2/tasks/{task_id}"),
    }
    assert ("GET", "/v2/asr/{task_id}") not in got


def test_common_route_inventory():
    for prefix in ("/v1", "/v2"):
        got = _route_set(common_routes.build_common_router(prefix))
        assert got == {("GET", f"{prefix}/health"), ("GET", f"{prefix}/capabilities")}


# ─── v1 / v2 响应一致 ───

def test_v1_v2_list_tasks_identical(make_client):
    tm = MagicMock()
    tm.list_tasks.return_value = [
        {"task_id": "t1", "status": "completed", "progress": 1.0, "language": "zh",
         "created_at": "2026-06-03T10:00:00", "finished_at": None, "error": None},
    ]
    client = make_client(task_manager=tm)
    assert client.get("/v1/tasks").json() == client.get("/v2/tasks").json()


def test_v1_v2_task_detail_identical(make_client):
    tm = MagicMock()
    tm.get_task.return_value = {"task_id": "t1", "status": "processing", "progress": 0.4,
                                "result": None, "error": None}
    client = make_client(task_manager=tm)
    assert client.get("/v1/tasks/t1").json() == client.get("/v2/tasks/t1").json()


def test_v1_v2_cancel_identical(make_client):
    tm = MagicMock()
    tm.cancel_task.return_value = "pending"
    client = make_client(task_manager=tm)
    assert client.delete("/v1/tasks/t1").json() == client.delete("/v2/tasks/t1").json()


# ─── health 向后兼容（旧 8 字段不变 + 新增 mode/capabilities）───

def test_health_backward_compatible_fields(make_client):
    legacy = {
        "status": "ready", "device": "cpu", "model_size": "0.6b",
        "align_enabled": False, "punc_enabled": True,
        "asr_backend": "qwen_asr", "vad_backend": "pytorch", "punc_backend": "pytorch",
    }
    client = make_client(service_info=dict(legacy))
    body = client.get("/v1/health").json()
    for k, v in legacy.items():
        assert body[k] == v          # 旧字段值不变
    assert body["mode"] == "standard"   # 新增字段默认值
    assert body["capabilities"] is None

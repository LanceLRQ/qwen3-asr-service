"""app/api/speaker_routes.py 测试（mock Service，TestClient，不触模型/库）。

覆盖：八条路由正反路径、401 鉴权、503（未启用 / model_tag 失配语义分裂）、
consent 缺失 400、ValueError→400 / SpeakerNotFoundError→404 /
SpeakerStoreError→500 映射、multipart 多文件、剩 0 模板提示。
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.config as cfg
from app.api.speaker_routes import build_speakers_router, init_speaker_routes
from app.runtime.speaker_store import SpeakerNotFoundError, SpeakerStoreError

AUTH = {"Authorization": "Bearer test-key"}

SPEAKER_ROW = {
    "id": "a" * 32, "name": "张三", "note": None, "source": "manual",
    "template_count": 2, "created_at": "t", "updated_at": "t",
}
SPEAKER_DETAIL = {
    "id": "a" * 32, "name": "张三", "note": "n", "source": "manual",
    "model_tag": "campplus_cn_common@v1", "created_at": "t", "updated_at": "t",
    "templates": [{"id": 1, "dur_sec": 5.0, "created_at": "t"}],
}


def make_client(service, tag_mismatch=False):
    init_speaker_routes(service, tag_mismatch=tag_mismatch)
    app = FastAPI()
    app.include_router(build_speakers_router())
    return TestClient(app)


def make_service():
    service = MagicMock()
    service.store = MagicMock()
    service.store.list_speakers.return_value = [SPEAKER_ROW]
    service.store.get_speaker.return_value = dict(SPEAKER_DETAIL)
    service.enroll.return_value = {"speaker_id": "a" * 32, "name": "张三", "templates": 1,
                                   "quality_hint": "建议提供 ≥3 个样本"}
    service.identify_file.return_value = {"matched": True, "speaker_id": "a" * 32,
                                          "name": "张三", "score": 0.62}
    service.add_template.return_value = {"speaker_id": "a" * 32, "templates": 2}
    return service


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr(cfg, "API_KEY", "test-key")


def _files(n=1):
    return [("files", (f"s{i}.wav", b"RIFFxxxx", "audio/wav")) for i in range(n)]


# ─── 鉴权 / 降级 ───

def test_requires_auth():
    c = make_client(make_service())
    assert c.get("/v2/speakers").status_code == 401                       # 无 token
    bad = {"Authorization": "Bearer wrong"}
    assert c.get("/v2/speakers", headers=bad).status_code == 401          # 错 token


def test_disabled_returns_503():
    c = make_client(None)
    r = c.get("/v2/speakers", headers=AUTH)
    assert r.status_code == 503 and r.json()["detail"] == "speaker_db_disabled"
    assert c.post("/v2/speakers/identify", headers=AUTH,
                  files={"file": ("a.wav", b"x", "audio/wav")}).status_code == 503


def test_tag_mismatch_splits_endpoints():
    """失配：登记/识别 503，管理端点（GET/PATCH/DELETE）保留（被遗忘权）。"""
    c = make_client(make_service(), tag_mismatch=True)
    r = c.post("/v2/speakers", headers=AUTH, files=_files(),
               data={"name": "x", "consent": "true"})
    assert r.status_code == 503 and r.json()["detail"] == "model_tag_mismatch"
    assert c.post("/v2/speakers/identify", headers=AUTH,
                  files={"file": ("a.wav", b"x", "audio/wav")}).status_code == 503
    assert c.get("/v2/speakers", headers=AUTH).status_code == 200
    assert c.patch(f"/v2/speakers/{'a'*32}", headers=AUTH,
                   json={"name": "y"}).status_code == 200
    assert c.delete(f"/v2/speakers/{'a'*32}", headers=AUTH).status_code == 200


# ─── enroll ───

def test_enroll_ok_multifile():
    service = make_service()
    c = make_client(service)
    r = c.post("/v2/speakers", headers=AUTH, files=_files(2),
               data={"name": "张三", "consent": "true", "note": "备注"})
    assert r.status_code == 201
    body = r.json()
    assert body["speaker_id"] == "a" * 32 and body["quality_hint"]
    args = service.enroll.call_args[0]
    assert args[0] == "张三" and args[1] == "备注" and len(args[2]) == 2


def test_enroll_missing_consent_400():
    c = make_client(make_service())
    r = c.post("/v2/speakers", headers=AUTH, files=_files(), data={"name": "x"})
    assert r.status_code == 400 and "consent" in r.json()["detail"]


def test_enroll_bad_extension_400():
    c = make_client(make_service())
    r = c.post("/v2/speakers", headers=AUTH,
               files=[("files", ("a.txt", b"x", "text/plain"))],
               data={"name": "x", "consent": "true"})
    assert r.status_code == 400 and "不支持的音频格式" in r.json()["detail"]


def test_enroll_value_error_maps_400():
    service = make_service()
    service.enroll.side_effect = ValueError("登记样本有效语音不足")
    c = make_client(service)
    r = c.post("/v2/speakers", headers=AUTH, files=_files(),
               data={"name": "x", "consent": "true"})
    assert r.status_code == 400 and "有效语音不足" in r.json()["detail"]


def test_enroll_store_error_maps_500():
    service = make_service()
    service.enroll.side_effect = SpeakerStoreError("登记写入失败")
    c = make_client(service)
    r = c.post("/v2/speakers", headers=AUTH, files=_files(),
               data={"name": "x", "consent": "true"})
    assert r.status_code == 500


# ─── 列表 / 详情 / 更新 / 删除 ───

def test_list_speakers():
    c = make_client(make_service())
    body = c.get("/v2/speakers", headers=AUTH).json()
    assert body["total"] == 1 and body["speakers"][0]["name"] == "张三"


def test_get_speaker_detail_and_404():
    service = make_service()
    c = make_client(service)
    body = c.get(f"/v2/speakers/{'a'*32}", headers=AUTH).json()
    assert body["templates"][0]["dur_sec"] == 5.0

    service.store.get_speaker.return_value = None
    assert c.get("/v2/speakers/nope", headers=AUTH).status_code == 404


def test_update_speaker_rename():
    service = make_service()
    c = make_client(service)
    r = c.patch(f"/v2/speakers/{'a'*32}", headers=AUTH, json={"name": "李四"})
    assert r.status_code == 200
    service.store.update_speaker.assert_called_once_with("a" * 32, "李四", None)


def test_update_speaker_missing_404():
    """无前置存在性检查：store 层 NotFound 即 404（TOCTOU 间隙同样落此映射）。"""
    service = make_service()
    service.store.update_speaker.side_effect = SpeakerNotFoundError("说话人不存在: nope")
    c = make_client(service)
    assert c.patch("/v2/speakers/nope", headers=AUTH,
                   json={"name": "李四"}).status_code == 404


def test_delete_speaker_calls_service():
    service = make_service()
    c = make_client(service)
    r = c.delete(f"/v2/speakers/{'a'*32}", headers=AUTH)
    assert r.status_code == 200 and r.json()["deleted"] is True
    service.delete_speaker.assert_called_once_with("a" * 32)


def test_delete_speaker_missing_404():
    service = make_service()
    service.delete_speaker.side_effect = SpeakerNotFoundError("说话人不存在: nope")
    c = make_client(service)
    assert c.delete("/v2/speakers/nope", headers=AUTH).status_code == 404


# ─── 模板 ───

def test_add_template():
    c = make_client(make_service())
    r = c.post(f"/v2/speakers/{'a'*32}/templates", headers=AUTH,
               files={"file": ("a.wav", b"x", "audio/wav")})
    assert r.status_code == 201 and r.json()["templates"] == 2


def test_delete_template_zero_remaining_hint():
    service = make_service()
    service.store.delete_template.return_value = 0
    c = make_client(service)
    body = c.delete(f"/v2/speakers/{'a'*32}/templates/1", headers=AUTH).json()
    assert body["remaining"] == 0 and "追加样本" in body["hint"]


def test_delete_template_missing_404():
    """404 走 NotFound 异常类型而非消息文本匹配（措辞改动不破坏路由语义）。"""
    service = make_service()
    service.store.delete_template.side_effect = SpeakerNotFoundError("模板不存在: x/9")
    c = make_client(service)
    assert c.delete(f"/v2/speakers/{'a'*32}/templates/9",
                    headers=AUTH).status_code == 404


def test_delete_template_other_error_500():
    service = make_service()
    service.store.delete_template.side_effect = SpeakerStoreError("磁盘写入失败")
    c = make_client(service)
    assert c.delete(f"/v2/speakers/{'a'*32}/templates/9",
                    headers=AUTH).status_code == 500


# ─── identify ───

def test_identify_matched():
    c = make_client(make_service())
    body = c.post("/v2/speakers/identify", headers=AUTH,
                  files={"file": ("a.wav", b"x", "audio/wav")}).json()
    assert body["matched"] is True and body["name"] == "张三"

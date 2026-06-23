"""app/runtime/speaker_service.py 单元测试（真 SpeakerStore 临时库 + fake 引擎/VAD）。

覆盖：enroll 质量门槛（时长/多人/consent）、模板均值入库、identify_file、
map_clusters 异常兜底、自动登记分支全集（过门槛/时长不足/开关关/序号递增/失败退回匿名）、
临时文件清理、留存音频。阈值只测逻辑分支（V0 标定铁律）。
"""
import os
import types

import numpy as np
import pytest

import app.config as cfg
from app.runtime.speaker_service import SpeakerService
from app.runtime.speaker_store import SpeakerStore

DIM = 192
TAG = "campplus_cn_common@v1"


def unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


class FakeEngine:
    """窗起点 < split_at 秒 → unit(vec_idx)，否则 unit(vec_idx+1)（制造多人样本）。"""

    def __init__(self, vec_idx=0, split_at=None):
        self.vec_idx = vec_idx
        self.split_at = split_at

    def embed_windows(self, wav, windows):
        out = []
        for st, _ in windows:
            i = self.vec_idx + (1 if self.split_at is not None and st >= self.split_at else 0)
            out.append(unit(i))
        return np.stack(out)


def make_vad(segments):
    return types.SimpleNamespace(detect=lambda p: segments)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离上传目录/服务根 + fake 音频 IO（convert 落空文件、sf.read 给 8s 假音频）。"""
    monkeypatch.setattr(cfg, "UPLOADS_DIR", str(tmp_path / "up"))
    monkeypatch.setattr(cfg, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr("app.runtime.speaker_service.convert_to_wav",
                        lambda src, dst: open(dst, "wb").write(b"x"))
    monkeypatch.setattr("app.runtime.speaker_service.sf.read",
                        lambda p, dtype=None: (np.zeros(16000 * 8, dtype=np.float32), 16000))
    return tmp_path


@pytest.fixture
def store(tmp_path):
    s = SpeakerStore(str(tmp_path / "speakers.db"), model_tag=TAG)
    yield s
    s.close()


def make_service(store, engine=None, vad_segments=((0, 5000),)):
    return SpeakerService(store, engine or FakeEngine(), make_vad(list(vad_segments)))


def _src(tmp_path, name="a.mp3"):
    p = tmp_path / name
    p.write_bytes(b"fake")
    return str(p)


# ─── enroll ───

def test_enroll_ok_with_quality_hint(env, store):
    svc = make_service(store)
    resp = svc.enroll("张三", "备注", [_src(env)], consent=True)
    assert len(resp["speaker_id"]) == 32
    assert resp["templates"] == 1
    assert "quality_hint" in resp                       # 模板 <3 提示
    assert store.get_speaker(resp["speaker_id"])["source"] == "manual"


def test_enroll_three_samples_no_hint(env, store):
    svc = make_service(store)
    resp = svc.enroll("张三", None, [_src(env, f"{i}.mp3") for i in range(3)], consent=True)
    assert resp["templates"] == 3
    assert "quality_hint" not in resp


def test_enroll_rejects_short_speech(env, store):
    svc = make_service(store, vad_segments=[(0, 2000)])   # 2s < 3s 门槛
    with pytest.raises(ValueError, match="有效语音不足"):
        svc.enroll("x", None, [_src(env)], consent=True)


def test_enroll_rejects_multi_speaker_sample(env, store):
    svc = make_service(store, engine=FakeEngine(split_at=2.5))   # 前后两人
    with pytest.raises(ValueError, match="多个说话人"):
        svc.enroll("x", None, [_src(env)], consent=True)


def test_enroll_requires_consent(env, store):
    svc = make_service(store)
    with pytest.raises(ValueError, match="consent"):
        svc.enroll("x", None, [_src(env)], consent=False)


def test_enroll_rejects_empty_files(env, store):
    with pytest.raises(ValueError, match="样本"):
        make_service(store).enroll("x", None, [], consent=True)


def test_temp_wavs_cleaned_on_success_and_failure(env, store):
    svc = make_service(store)
    svc.enroll("a", None, [_src(env)], consent=True)
    svc_fail = make_service(store, vad_segments=[(0, 1000)])
    with pytest.raises(ValueError):
        svc_fail.enroll("b", None, [_src(env)], consent=True)
    leftovers = [f for f in os.listdir(cfg.UPLOADS_DIR) if f.startswith("spk_")]
    assert leftovers == []


def test_store_audio_kept_and_removed_on_delete(env, store, monkeypatch):
    monkeypatch.setattr(cfg, "SPEAKER_STORE_AUDIO", True)
    svc = make_service(store)
    sid = svc.enroll("a", None, [_src(env)], consent=True)["speaker_id"]
    audio_dir = os.path.join(str(env), "data", "speaker_audio", sid)
    assert os.path.isfile(os.path.join(audio_dir, "00.wav"))
    svc.delete_speaker(sid)
    assert not os.path.isdir(audio_dir)                  # 被遗忘权：音频同步清理


# ─── identify_file ───

def test_identify_file_hit_and_miss(env, store):
    svc = make_service(store)
    sid = svc.enroll("张三", None, [_src(env)], consent=True)["speaker_id"]
    hit = svc.identify_file(_src(env, "q.mp3"))
    assert hit["matched"] is True and hit["speaker_id"] == sid and hit["name"] == "张三"

    svc_other = make_service(store, engine=FakeEngine(vec_idx=5))
    assert svc_other.identify_file(_src(env, "q2.mp3")) == {"matched": False}


# ─── map_clusters（实时联动：仅识别）───

def test_map_clusters_hit_and_miss(env, store):
    svc = make_service(store)
    sid = svc.enroll("张三", None, [_src(env)], consent=True)["speaker_id"]
    out = svc.map_clusters([
        {"label": "A", "centroid": unit(0)},
        {"label": "B", "centroid": unit(7)},
    ])
    assert out[0]["speaker_id"] == sid and out[0]["name"] == "张三"
    assert out[1] == {"label": "B", "speaker_id": None, "name": None, "score": None}


def test_map_clusters_exception_falls_back_anonymous(env, store):
    svc = make_service(store)
    out = svc.map_clusters([{"label": "A"}])             # 缺 centroid → 内部异常
    assert out == [{"label": "A", "speaker_id": None, "name": None, "score": None}]


def test_map_clusters_never_auto_enrolls(env, store):
    svc = make_service(store)
    svc.map_clusters([{"label": "A", "centroid": unit(3), "dur_sec": 99.0}])
    assert store.speaker_count == 0                      # 实时路径绝不建档


# ─── map_and_enroll_clusters（离线联动：识别 + 自动登记）───

def _cluster(label, vec, dur):
    return {"label": label, "centroid": vec, "dur_sec": dur}


def test_auto_enroll_above_threshold(env, store):
    svc = make_service(store)
    out = svc.map_and_enroll_clusters([_cluster("A", unit(0), 12.0)])
    assert out[0]["name"] == "说话人_01" and out[0]["auto_enrolled"] is True
    assert store.get_speaker(out[0]["speaker_id"])["source"] == "auto"


def test_auto_enroll_sequence_increments(env, store):
    svc = make_service(store)
    out = svc.map_and_enroll_clusters([
        _cluster("A", unit(0), 12.0), _cluster("B", unit(1), 15.0),
    ])
    assert [m["name"] for m in out] == ["说话人_01", "说话人_02"]


def test_auto_enroll_below_duration_stays_anonymous(env, store):
    svc = make_service(store)
    out = svc.map_and_enroll_clusters([_cluster("A", unit(0), 5.0)])  # <10s
    assert out[0]["speaker_id"] is None
    assert store.speaker_count == 0


def test_auto_enroll_disabled_stays_anonymous(env, store, monkeypatch):
    monkeypatch.setattr(cfg, "SPEAKER_AUTO_ENROLL", False)
    svc = make_service(store)
    out = svc.map_and_enroll_clusters([_cluster("A", unit(0), 12.0)])
    assert out[0]["speaker_id"] is None
    assert store.speaker_count == 0


def test_auto_enroll_hit_does_not_re_enroll(env, store):
    svc = make_service(store)
    sid = svc.enroll("张三", None, [_src(env)], consent=True)["speaker_id"]
    out = svc.map_and_enroll_clusters([_cluster("A", unit(0), 12.0)])
    assert out[0]["speaker_id"] == sid and out[0]["name"] == "张三"
    assert store.speaker_count == 1                      # 命中不重复建档（防投毒）


def test_auto_enroll_failure_falls_back_anonymous(env, store):
    svc = make_service(store)
    store.close()                                        # alloc/enroll 将失败
    out = svc.map_and_enroll_clusters([_cluster("A", unit(0), 12.0)])
    assert out[0]["speaker_id"] is None                  # 退回匿名，不抛错


# ─── enroll_cluster：实时显式/自动登记从会话质心入库（本特性）───

def test_enroll_cluster_creates_speaker(env, store):
    svc = make_service(store)
    sid = svc.enroll_cluster("李四", unit(3), 8.0, consent=True, source="manual")
    info = store.get_speaker(sid)
    assert info["name"] == "李四"
    assert info["source"] == "manual"
    assert len(info["templates"]) == 1
    # 入库质心可被 1:N 识别回命中
    hit = store.identify(unit(3), threshold=cfg.SPEAKER_ID_THRESHOLD,
                         margin=cfg.SPEAKER_ID_MARGIN)
    assert hit is not None and hit["speaker_id"] == sid


def test_enroll_cluster_requires_consent(env, store):
    svc = make_service(store)
    with pytest.raises(Exception):
        svc.enroll_cluster("李四", unit(3), 8.0, consent=False)
    assert store.speaker_count == 0

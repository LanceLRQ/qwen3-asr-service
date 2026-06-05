"""app/runtime/speaker_store.py 单元测试（真 SQLite 临时库，合成向量，不触模型）。

覆盖：建库幂等、enroll/CRUD、consent CHECK、维度/归一校验、级联删除+vacuum、
缓存重载一致性（写后 identify 立即可见）、identify 阈值/margin/空库、
SpeakerStoreError 上抛语义、model_tag、占位名序号、永不自动清理（无清理方法）。
阈值仅测逻辑分支，不写精度断言（V0 标定铁律）。
"""
import sqlite3

import numpy as np
import pytest

from app.runtime.speaker_store import (
    SpeakerNotFoundError,
    SpeakerStore,
    SpeakerStoreError,
)

DIM = 192
TAG = "campplus_cn_common@v1"


def unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def mix(a, b, wa, wb) -> np.ndarray:
    v = wa * a + wb * b
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def store(tmp_path):
    s = SpeakerStore(str(tmp_path / "speakers.db"), model_tag=TAG)
    yield s
    s.close()


# ─── 建库 / meta ───

def test_init_idempotent(tmp_path):
    path = str(tmp_path / "s.db")
    s1 = SpeakerStore(path, model_tag=TAG)
    sid = s1.enroll_speaker("张三", None, [unit(0)], [5.0], consent=True)
    s1.close()
    s2 = SpeakerStore(path, model_tag=TAG)     # 重开同库：DDL 幂等、数据仍在
    assert s2.get_speaker(sid)["name"] == "张三"
    assert s2.speaker_count == 1
    s2.close()


def test_check_model_tag(store):
    assert store.check_model_tag(TAG) is True
    assert store.check_model_tag("other@v2") is False


def test_no_auto_cleanup_api(store):
    # 永不自动清理（2026-06-05 需求定稿）：不应存在任何 TTL/清理方法
    assert not hasattr(store, "cleanup_expired")
    assert not hasattr(store, "close_dangling")


# ─── enroll / 校验 ───

def test_enroll_returns_uuid_hex_and_visible(store):
    sid = store.enroll_speaker("张三", "备注", [unit(0), mix(unit(0), unit(1), 0.99, 0.14)],
                               [5.0, 6.0], consent=True)
    assert len(sid) == 32 and all(c in "0123456789abcdef" for c in sid)
    info = store.get_speaker(sid)
    assert info["name"] == "张三" and info["note"] == "备注"
    assert info["source"] == "manual" and len(info["templates"]) == 2
    listed = store.list_speakers()
    assert listed[0]["id"] == sid and listed[0]["template_count"] == 2


def test_enroll_requires_consent(store):
    with pytest.raises(SpeakerStoreError, match="consent"):
        store.enroll_speaker("x", None, [unit(0)], [5.0], consent=False)


def test_consent_check_constraint_in_schema(store):
    # 双保险第二层：绕过应用校验直插 consent=0 → schema CHECK 拒绝
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO speakers(id, name, consent, source, model_tag, centroid,"
            " created_at, updated_at) VALUES('x','x',0,'manual',?,?,'t','t')",
            (TAG, unit(0).tobytes()),
        )


def test_enroll_rejects_bad_dim(store):
    with pytest.raises(SpeakerStoreError, match="维度"):
        store.enroll_speaker("x", None, [np.zeros(64, dtype=np.float32)], [5.0], consent=True)


def test_enroll_rejects_unnormalized(store):
    with pytest.raises(SpeakerStoreError, match="归一"):
        store.enroll_speaker("x", None, [unit(0) * 2.0], [5.0], consent=True)


def test_enroll_rejects_empty_or_mismatched(store):
    with pytest.raises(SpeakerStoreError):
        store.enroll_speaker("x", None, [], [], consent=True)
    with pytest.raises(SpeakerStoreError):
        store.enroll_speaker("x", None, [unit(0)], [5.0, 6.0], consent=True)


def test_auto_enroll_source_recorded(store):
    sid = store.enroll_speaker(store.alloc_auto_name(), None, [unit(0)], [12.0],
                               consent=True, source="auto")
    assert store.get_speaker(sid)["source"] == "auto"
    assert store.list_speakers()[0]["source"] == "auto"


# ─── 占位名序号 ───

def test_alloc_auto_name_sequence_no_reuse(store):
    assert store.alloc_auto_name() == "说话人_01"
    sid = store.enroll_speaker("说话人_02", None, [unit(1)], [12.0],
                               consent=True, source="auto")
    assert store.alloc_auto_name() == "说话人_02"  # noqa: 序号与名字无关，按 meta 自增
    store.delete_speaker(sid)
    assert store.alloc_auto_name() == "说话人_03"  # 删除不复用序号


# ─── identify：阈值 / margin / 空库 / 写后可见 ───

def test_identify_empty_db(store):
    assert store.identify(unit(0)) is None


def test_identify_hit_and_threshold(store):
    sid = store.enroll_speaker("张三", None, [unit(0)], [5.0], consent=True)
    hit = store.identify(unit(0), threshold=0.45, margin=0.10)
    assert hit["speaker_id"] == sid and hit["name"] == "张三"
    assert hit["score"] == pytest.approx(1.0, abs=1e-5)
    # 低于阈值 → unknown
    assert store.identify(unit(1), threshold=0.45, margin=0.10) is None


def test_identify_margin_rejects_close_competitors(store):
    store.enroll_speaker("A", None, [unit(0)], [5.0], consent=True)
    # B 的质心与 A 高度相近：查询 e0 时 top1-top2 < margin → 宁缺勿错
    store.enroll_speaker("B", None, [mix(unit(0), unit(1), 0.95, 0.312)], [5.0], consent=True)
    assert store.identify(unit(0), threshold=0.45, margin=0.10) is None
    # margin 收紧到 0.01 时可命中
    assert store.identify(unit(0), threshold=0.45, margin=0.01)["name"] == "A"


def test_identify_single_speaker_skips_margin(store):
    """库内仅 1 人时无第二名可比，margin 无定义——单靠 threshold 门控（有意设计）。"""
    sid = store.enroll_speaker("独苗", None, [unit(0)], [5.0], consent=True)
    # 与质心余弦 0.8：双人场景下若有近邻会被 margin 拦截，单人场景直接命中
    q = mix(unit(0), unit(1), 0.8, 0.6)
    hit = store.identify(q, threshold=0.45, margin=0.99)   # margin 给到极端值也不参与
    assert hit["speaker_id"] == sid


def test_not_found_is_dedicated_subclass(store):
    """不存在类错误抛 SpeakerNotFoundError（路由层 404 依赖异常类型而非消息文本）。"""
    assert issubclass(SpeakerNotFoundError, SpeakerStoreError)
    with pytest.raises(SpeakerNotFoundError):
        store.update_speaker("nope", name="x")
    with pytest.raises(SpeakerNotFoundError):
        store.delete_speaker("nope")
    with pytest.raises(SpeakerNotFoundError):
        store.delete_template("nope", 1)
    with pytest.raises(SpeakerNotFoundError):
        store.add_template("nope", unit(0), 5.0)


def test_delete_evicts_cache_when_reload_fails(store, monkeypatch):
    """DELETE 落库后缓存重载失败：内存手术摘除，不留幻影命中（被遗忘权）。"""
    sid_a = store.enroll_speaker("甲", None, [unit(0)], [5.0], consent=True)
    sid_b = store.enroll_speaker("乙", None, [unit(1)], [5.0], consent=True)

    def boom():
        raise SpeakerStoreError("reload boom")

    monkeypatch.setattr(store, "_reload_cache", boom)
    store.delete_speaker(sid_a)                       # 重载失败但删除成功，不上抛
    assert store.speaker_count == 1
    assert store.identify(unit(0), threshold=0.45) is None        # 已删者不可再命中
    assert store.identify(unit(1), threshold=0.45)["speaker_id"] == sid_b


def test_identify_visible_immediately_after_write(store):
    v0 = store.cache_version
    sid = store.enroll_speaker("新人", None, [unit(3)], [5.0], consent=True)
    assert store.cache_version > v0
    assert store.identify(unit(3))["speaker_id"] == sid
    store.delete_speaker(sid)
    assert store.identify(unit(3)) is None     # 删后立即不可命中


# ─── 模板操作 / 质心重算 ───

def test_add_template_recomputes_centroid(store):
    sid = store.enroll_speaker("张三", None, [unit(0)], [5.0], consent=True)
    store.add_template(sid, unit(1), 6.0)
    # 质心 = normalize(mean(e0,e1))：与 normalize(e0+e1) 同向，得分≈1
    q = mix(unit(0), unit(1), 1.0, 1.0)
    assert store.identify(q, threshold=0.45)["score"] == pytest.approx(1.0, abs=1e-4)
    assert len(store.get_speaker(sid)["templates"]) == 2


def test_add_template_missing_speaker(store):
    with pytest.raises(SpeakerStoreError, match="不存在"):
        store.add_template("nope", unit(0), 5.0)


def test_template_cap(store):
    sid = store.enroll_speaker("x", None, [unit(0)], [5.0], consent=True)
    for i in range(SpeakerStore.MAX_TEMPLATES - 1):
        store.add_template(sid, unit(0), 5.0)
    with pytest.raises(SpeakerStoreError, match="上限"):
        store.add_template(sid, unit(0), 5.0)


def test_delete_template_remaining_and_keep_speaker(store):
    sid = store.enroll_speaker("x", None, [unit(0), unit(1)], [5.0, 6.0], consent=True)
    tpl_ids = [t["id"] for t in store.get_speaker(sid)["templates"]]
    assert store.delete_template(sid, tpl_ids[0]) == 1
    # 质心重算为剩余模板：e1 方向
    assert store.identify(unit(1), threshold=0.45)["speaker_id"] == sid
    assert store.delete_template(sid, tpl_ids[1]) == 0
    assert store.get_speaker(sid) is not None  # 剩 0 模板不自动删人


def test_delete_template_missing(store):
    sid = store.enroll_speaker("x", None, [unit(0)], [5.0], consent=True)
    with pytest.raises(SpeakerStoreError, match="不存在"):
        store.delete_template(sid, 9999)


# ─── 更新 / 删除 ───

def test_update_speaker_rename_reflected_in_identify(store):
    sid = store.enroll_speaker("说话人_01", None, [unit(0)], [12.0],
                               consent=True, source="auto")
    store.update_speaker(sid, name="张三", note="改名")
    info = store.get_speaker(sid)
    assert info["name"] == "张三" and info["note"] == "改名"
    assert store.identify(unit(0))["name"] == "张三"   # 缓存同步重载


def test_update_speaker_missing(store):
    with pytest.raises(SpeakerStoreError, match="不存在"):
        store.update_speaker("nope", name="x")


def test_delete_speaker_cascades_templates(store):
    sid = store.enroll_speaker("x", None, [unit(0), unit(1)], [5.0, 6.0], consent=True)
    store.delete_speaker(sid)
    assert store.get_speaker(sid) is None
    n = store._conn.execute(
        "SELECT COUNT(*) AS n FROM templates WHERE speaker_id=?", (sid,)).fetchone()["n"]
    assert n == 0                              # ON DELETE CASCADE 生效
    assert store.speaker_count == 0


def test_delete_speaker_missing(store):
    with pytest.raises(SpeakerStoreError, match="不存在"):
        store.delete_speaker("nope")


# ─── 审计旁路语义 ───

def test_audit_rows_written(store):
    sid = store.enroll_speaker("x", None, [unit(0)], [5.0], consent=True)
    store.update_speaker(sid, name="y")
    store.delete_speaker(sid)
    actions = [r["action"] for r in
               store._conn.execute("SELECT action FROM audit_log ORDER BY id").fetchall()]
    assert actions == ["enroll", "update", "delete"]


def test_audit_failure_does_not_raise(store):
    store.close()
    store.audit("enroll", "x")                 # 连接已关：仅 WARN，不上抛

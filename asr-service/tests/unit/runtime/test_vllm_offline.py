"""vLLM 离线处理器单元测试（mock engine，不依赖 vLLM/GPU/ffmpeg）。

覆盖：词间隙分段 / 整文兜底 / max_segment 二次切 / warnings 生成 / run_vllm_offline
端到端（result schema 同 standard、progress、cancelled 早退、with_words 透传）。
dependency-neutral：standard venv 即可运行。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from app.runtime import vllm_offline as vo
from app import config as cfg


class _Engine:
    """最小 mock 引擎：align_enabled + split_chunks（等分）+ transcribe 记录调用并回放结果。

    chunk_results 给定则按其数量切块、逐块回放对应结果（验证逐块转写/进度/合并）；
    否则每次 transcribe 回放同一 result（单块/短音频路径）。
    """

    def __init__(self, align=True, result=None, chunk_results=None):
        self._align = align
        self._result = result or []
        self._chunk_results = chunk_results
        self._n_chunks = len(chunk_results) if chunk_results else 1
        self.transcribe_calls = []

    @property
    def align_enabled(self):
        return self._align

    def split_chunks(self, wav, sr, chunk_sec):
        n = self._n_chunks
        L = max(1, len(wav) // n)
        return [(wav[i * L:(i + 1) * L] if i < n - 1 else wav[i * L:], round(i * L / sr, 3))
                for i in range(n)]

    def transcribe(self, audio, language=None, with_words=False):
        self.transcribe_calls.append((audio, language, with_words))
        if self._chunk_results is not None:
            return self._chunk_results[len(self.transcribe_calls) - 1]
        return self._result


def _trans(text, items=None):
    """构造 ASRTranscription 形态：.text + .time_stamps.items[].{text,start_time,end_time}。"""
    ts = None
    if items:
        ts = SimpleNamespace(items=[
            SimpleNamespace(text=t, start_time=s, end_time=e) for t, s, e in items])
    return SimpleNamespace(text=text, time_stamps=ts, language="Chinese")


# ── _segment ──────────────────────────────────────────────
def test_segment_by_sentence():
    """标点优先（主路径）：按句末标点 。！？ 切句，段文本含标点、精确平铺。"""
    full = "哎呦。王处？辛苦！"
    words = [
        {"text": "哎", "start": 0.0, "end": 0.2}, {"text": "呦", "start": 0.2, "end": 0.4},
        {"text": "王", "start": 1.0, "end": 1.2}, {"text": "处", "start": 1.2, "end": 1.4},
        {"text": "辛", "start": 2.0, "end": 2.2}, {"text": "苦", "start": 2.2, "end": 2.4},
    ]
    segs = vo._segment(full, words, 3.0, None)
    assert [s["text"] for s in segs] == ["哎呦。", "王处？", "辛苦！"]
    assert "".join(s["text"] for s in segs) == full
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 0.4 and len(segs[0]["words"]) == 2


def test_segment_word_gap_fallback():
    """无句末标点（罕见）→ 退化为词间隙分段（间隙 0.8s > 0.5 断段）。"""
    words = [
        {"text": "你", "start": 0.0, "end": 0.2},
        {"text": "好", "start": 0.25, "end": 0.4},   # 间隙 0.05s → 同段
        {"text": "世", "start": 1.2, "end": 1.4},     # 间隙 0.8s > 0.5 → 新段
        {"text": "界", "start": 1.45, "end": 1.6},
    ]
    segs = vo._segment("你好世界", words, 2.0, None)
    assert [s["text"] for s in segs] == ["你好", "世界"]
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 0.4 and len(segs[0]["words"]) == 2


def test_segment_whole_text_fallback():
    assert vo._segment("整段", None, 3.0, None) == [{"start": 0.0, "end": 3.0, "text": "整段"}]


def test_segment_empty_text():
    assert vo._segment("", None, 1.0, None) == []


def test_segment_max_segment_cap(monkeypatch):
    monkeypatch.setattr(cfg, "VLLM_SEGMENT_GAP_MS", 2000)   # 间隙阈值很大 → 不靠间隙断
    words = [{"text": str(i), "start": i * 0.3, "end": i * 0.3 + 0.2} for i in range(10)]  # 跨度 ~2.9s
    segs = vo._segment("0123456789", words, 3.0, 1.0)        # max_segment=1s 强制二次切
    assert len(segs) >= 2
    assert all((s["end"] - s["start"]) <= 1.0 + 0.3 for s in segs)


def test_segment_preserves_punctuation():
    """段文本取自 full_text 切片 → 保留模型原生标点；逗号不单独成段（句级），精确平铺。"""
    full = "你好，世界。再见！"
    words = [
        {"text": "你", "start": 0.0, "end": 0.2},
        {"text": "好", "start": 0.25, "end": 0.4},
        {"text": "世", "start": 1.2, "end": 1.4},
        {"text": "界", "start": 1.45, "end": 1.6},
        {"text": "再", "start": 2.5, "end": 2.7},
        {"text": "见", "start": 2.75, "end": 2.9},
    ]
    segs = vo._segment(full, words, 3.0, None)
    assert "".join(s["text"] for s in segs) == full          # 精确平铺
    assert [s["text"] for s in segs] == ["你好，世界。", "再见！"]   # 句级：逗号不断


def test_segment_long_sentence_comma_subsplit():
    """超 max_segment 的长句在逗号处二次切。"""
    full = "甲，乙，丙。"
    words = [
        {"text": "甲", "start": 0.0, "end": 0.2},
        {"text": "乙", "start": 3.0, "end": 3.2},
        {"text": "丙", "start": 6.0, "end": 6.2},     # 整句跨度 6.2s > max_segment=5 → 按逗号切
    ]
    segs = vo._segment(full, words, 7.0, 5)
    assert "".join(s["text"] for s in segs) == full
    assert [s["text"] for s in segs] == ["甲，", "乙，", "丙。"]


def test_segment_clamps_corrupt_duration():
    """对齐器时间戳回退/过摊致段跨度异常 → end 钳制、无负时长、文本仍完整。"""
    full = "前后。"                                    # 单句，含句末标点
    words = [
        {"text": "前", "start": 100.0, "end": 100.2},
        {"text": "后", "start": 60.0, "end": 60.2},    # 回退：min/max 跨度 40.2s
    ]
    segs = vo._segment(full, words, 200.0, 5)
    assert len(segs) == 1
    assert segs[0]["text"] == "前后。"
    assert segs[0]["end"] >= segs[0]["start"]            # 无负时长
    assert segs[0]["end"] - segs[0]["start"] <= 5 + 0.01  # 钳制到 max_segment


# ── _collect_warnings ─────────────────────────────────────
def test_warnings_all():
    w = vo._collect_warnings(
        _Engine(align=False),
        {"with_punc": False, "with_words": True, "diarize": True, "speaker_id_threshold": 0.5},
        identify_speakers=True)
    assert set(w) == {"with_punc", "with_words", "diarize",
                      "identify_speakers", "speaker_id_threshold/margin"}


def test_warnings_clean_when_align_on():
    # 对齐器开 + 仅请求 words → 无 warning
    assert vo._collect_warnings(_Engine(align=True), {"with_words": True}, False) == []


def test_warnings_diarize_suppressed_when_speaker_on():
    """说话人引擎挂载（speaker_enabled）→ diarize 不再软提示（Phase 2）。"""
    w = vo._collect_warnings(_Engine(align=True), {"diarize": True}, False,
                             speaker_enabled=True, spk_id_ready=False)
    assert "diarize" not in w


def test_warnings_identify_when_no_speaker_db():
    """开了说话人分离但无声纹库（spk_id_ready=False）→ identify/id 阈值软提示。"""
    w = vo._collect_warnings(
        _Engine(align=True),
        {"diarize": True, "speaker_id_threshold": 0.5}, True,
        speaker_enabled=True, spk_id_ready=False)
    assert "diarize" not in w
    assert "identify_speakers" in w
    assert "speaker_id_threshold/margin" in w


# ── 说话人分离 / 识别（Phase 2，mock 引擎/服务，复用真实 speaker_cluster）──
class _SpeakerEngine:
    """mock CAM++：所有窗回放同一归一化向量 → cluster_offline 归为单簇 A。"""
    EMB_DIM = 192

    def embed_windows(self, wav, windows):
        v = np.zeros((len(windows), self.EMB_DIM), dtype=np.float32)
        v[:, 0] = 1.0
        return v


class _EnergyVAD:
    def detect_array(self, wav, sr):
        return [(0, 5000)]


class _SpeakerService:
    def __init__(self):
        self.calls = []

    def map_and_enroll_clusters(self, clusters, *, id_threshold=None, id_margin=None):
        self.calls.append((clusters, id_threshold, id_margin))
        return [{"label": "A", "speaker_id": "sp1", "name": "张三", "score": 0.9}]


@pytest.fixture
def patched_spk(monkeypatch, tmp_path):
    """写真实 5s 静音 wav（_diarize 需 sf.read 真文件）；embed_windows 已 mock 不读内容。"""
    import soundfile as sf
    monkeypatch.setattr(cfg, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(vo, "convert_to_wav",
                        lambda i, o: sf.write(o, np.zeros(16000 * 5, dtype="float32"), 16000))
    monkeypatch.setattr(vo, "get_audio_duration", lambda p: 5.0)
    return monkeypatch


def _spk_trans():
    return [_trans("你好。世界。",
                   [("你", 0.0, 0.2), ("好", 0.25, 0.4), ("世", 1.2, 1.4), ("界", 1.45, 1.6)])]


def test_run_with_diarization(patched_spk):
    """speaker_engine 在 + diarize → 每段叠加 speaker，result.speakers=['A']，无 diarize 软提示。"""
    eng = _Engine(align=True, result=_spk_trans())
    task = {"task_id": "d1", "file_path": "/x.wav", "options": {"diarize": True}}
    r = vo.run_vllm_offline(eng, task, speaker_engine=_SpeakerEngine(), energy_vad=_EnergyVAD())

    assert r["speakers"] == ["A"]
    assert len(r["segments"]) == 2
    assert all(s.get("speaker") == "A" for s in r["segments"])
    assert "diarize" not in r.get("warnings", [])


def test_run_with_identification(patched_spk):
    """identify_speakers + 声纹库 → speakers 升级为映射，段叠加 speaker_name。"""
    eng = _Engine(align=True, result=_spk_trans())
    svc = _SpeakerService()
    task = {"task_id": "i1", "file_path": "/x.wav", "identify_speakers": True,
            "options": {"diarize": True, "speaker_id_threshold": 0.4}}
    r = vo.run_vllm_offline(eng, task, speaker_engine=_SpeakerEngine(),
                            speaker_service=svc, energy_vad=_EnergyVAD())

    assert r["speakers"] == [{"label": "A", "speaker_id": "sp1", "name": "张三", "score": 0.9}]
    assert all(s.get("speaker_name") == "张三" for s in r["segments"])
    assert svc.calls and svc.calls[0][1] == 0.4        # id_threshold 透传


def test_run_diarize_disabled_no_speakers(patched_spk):
    """diarize=false → 不分离，无 speaker 字段、无 speakers。"""
    eng = _Engine(align=True, result=_spk_trans())
    task = {"task_id": "d2", "file_path": "/x.wav", "options": {"diarize": False}}
    r = vo.run_vllm_offline(eng, task, speaker_engine=_SpeakerEngine(), energy_vad=_EnergyVAD())

    assert "speakers" not in r
    assert all("speaker" not in s for s in r["segments"])


def test_run_no_speaker_engine_warns_diarize(patched_spk):
    """未挂说话人引擎但请求 diarize → 软提示，转写不受影响。"""
    eng = _Engine(align=True, result=_spk_trans())
    task = {"task_id": "d3", "file_path": "/x.wav", "options": {"diarize": True}}
    r = vo.run_vllm_offline(eng, task)        # speaker_engine=None

    assert "speakers" not in r
    assert "diarize" in r["warnings"]


# ── run_vllm_offline 端到端 ────────────────────────────────
@pytest.fixture
def patched(monkeypatch, tmp_path):
    """写真实 5s wav（_transcribe_progressive 需 sf.read）；默认时长 5s ≤ 切块阈值=单块直转。"""
    import soundfile as sf
    monkeypatch.setattr(cfg, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(vo, "convert_to_wav",
                        lambda i, o: sf.write(o, np.zeros(16000 * 5, dtype="float32"), 16000))
    monkeypatch.setattr(vo, "get_audio_duration", lambda p: 5.0)
    return monkeypatch


def test_run_with_words(patched):
    eng = _Engine(align=True, result=[_trans(
        "你好。世界。", [("你", 0.0, 0.2), ("好", 0.25, 0.4), ("世", 1.2, 1.4), ("界", 1.45, 1.6)])])
    prog = []
    task = {"task_id": "t1", "file_path": "/x.wav", "language": "zh",
            "options": {"with_words": True}}
    r = vo.run_vllm_offline(eng, task, progress_callback=prog.append)

    assert r["full_text"] == "你好。世界。"
    assert r["align_enabled"] is True and r["punc_enabled"] is True
    assert r["language"] == "zh"
    assert len(r["segments"]) == 2 and r["segments"][0]["words"]    # 按句切：你好。| 世界。
    assert "warnings" not in r
    assert prog[-1] == 1.0
    assert eng.transcribe_calls[0][2] is True          # with_words 透传


def test_run_no_align_fallback(patched):
    eng = _Engine(align=False, result=[_trans("整段文本。")])
    task = {"task_id": "t2", "file_path": "/x.wav", "options": {"with_words": True}}
    r = vo.run_vllm_offline(eng, task)

    assert r["align_enabled"] is False
    assert len(r["segments"]) == 1 and "words" not in r["segments"][0]
    assert r["segments"][0]["text"] == "整段文本。"
    assert "with_words" in r["warnings"]               # 请求 words 但无对齐器
    assert eng.transcribe_calls[0][2] is False         # align off → 不透传 with_words


def test_run_cancelled_before_transcribe(patched):
    eng = _Engine(align=True, result=[_trans("不应产生")])
    task = {"task_id": "t3", "file_path": "/x.wav", "options": {}}
    r = vo.run_vllm_offline(eng, task, cancelled=lambda: True)

    assert r["segments"] == [] and r["full_text"] == ""
    assert eng.transcribe_calls == []                  # 取消 → 未触发推理


# ── 长音频逐块转写（进度 / 合并 / 取消粒度）──
def _prog_patch(monkeypatch, tmp_path, duration):
    import soundfile as sf
    monkeypatch.setattr(cfg, "UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(vo, "convert_to_wav",
                        lambda i, o: sf.write(o, np.zeros(16000 * 6, dtype="float32"), 16000))
    monkeypatch.setattr(vo, "get_audio_duration", lambda p: duration)


def test_run_transcribe_progressive(monkeypatch, tmp_path):
    """长音频(>切块阈值)逐块转写：N 次 transcribe、转写阶段多点递增进度、文本按块合并。"""
    _prog_patch(monkeypatch, tmp_path, duration=600.0)            # >180 → 切块
    eng = _Engine(align=False, chunk_results=[
        [_trans("第一段。")], [_trans("第二段。")], [_trans("第三段。")]])
    prog = []
    task = {"task_id": "p1", "file_path": "/x.wav", "options": {"with_words": False}}
    r = vo.run_vllm_offline(eng, task, progress_callback=prog.append)

    assert len(eng.transcribe_calls) == 3                        # 逐块
    assert r["full_text"] == "第一段。第二段。第三段。"               # 按块合并（""join）
    mid = [p for p in prog if 0.1 < p < 0.85]
    assert len(mid) >= 2 and mid == sorted(mid)                  # 转写阶段多点递增
    assert prog[-1] == 1.0


def test_run_transcribe_progressive_words_offset(monkeypatch, tmp_path):
    """逐块词级时间戳按块 offset 归到绝对时间（第二块 +~3s）。"""
    _prog_patch(monkeypatch, tmp_path, duration=400.0)           # >180 → 2 块（6s 等分→offset≈3s）
    eng = _Engine(align=True, chunk_results=[
        [_trans("你好。", [("你", 0.0, 0.2), ("好", 0.2, 0.4)])],
        [_trans("世界。", [("世", 0.0, 0.2), ("界", 0.2, 0.4)])]])
    task = {"task_id": "p2", "file_path": "/x.wav", "options": {"with_words": True}}
    r = vo.run_vllm_offline(eng, task)

    assert len(eng.transcribe_calls) == 2
    assert r["full_text"] == "你好。世界。"
    all_words = [w for s in r["segments"] for w in s.get("words", [])]
    assert any(w["start"] >= 3.0 for w in all_words)             # 第2块 offset(~3s)+词时间


def test_run_transcribe_progressive_cancel_midway(monkeypatch, tmp_path):
    """逐块转写途中取消：已转写块后即停，结果为空、未跑完所有块。"""
    _prog_patch(monkeypatch, tmp_path, duration=600.0)
    eng = _Engine(align=False, chunk_results=[[_trans("一")], [_trans("二")], [_trans("三")]])

    class _CancelAfter:
        def __init__(self, n):
            self.calls, self.n = 0, n

        def __call__(self):
            self.calls += 1
            return self.calls > self.n

    # 调用序：pre-check(1,False) → chunk0 前(2,False)→转写 → chunk1 前(3,True)→停
    task = {"task_id": "p3", "file_path": "/x.wav", "options": {"with_words": False}}
    r = vo.run_vllm_offline(eng, task, cancelled=_CancelAfter(2))

    assert r["full_text"] == "" and r["segments"] == []
    assert len(eng.transcribe_calls) == 1                        # 只转写首块即被取消

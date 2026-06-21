"""app/utils/arg_schema.py 单一参数 schema 测试（C1：零行为变化重构）。

核心断言：argparse 全 SUPPRESS——未传的参数不出现在 Namespace（覆盖语义的根基），
schema 默认值与重构前 argparse 散落默认值逐项一致。
"""
import pytest

from app.utils.arg_schema import ARG_SPECS, build_parser, schema_defaults


# 重构前 main.py argparse 的默认值表（dest 键），作为零行为变化的基准
LEGACY_DEFAULTS = {
    "serve_mode": "standard",
    "device": "auto",
    "model_size": None,
    "enable_align": True,
    "enable_punc": False,
    "model_source": "modelscope",
    "host": None,
    "port": None,
    "web": False,
    "max_segment": 5,
    "api_key": None,
    "max_queue_size": None,
    "enable_stream": False,
    "max_stream_sessions": None,
    "stream_asr_concurrency": None,
    # ── 远场过滤新增，非重构前遗留 ──
    "vad_speech_noise_thres": 0.6,
    "stream_noise_filter": False,
    "stream_energy_floor_dbfs": -50.0,
    "stream_snr_min_db": 6.0,
    # ── P 系列（任务持久化）新增，非重构前遗留 ──
    "enable_task_store": False,
    "task_db_path": "data/tasks.db",
    "task_retention_days": 7,
    # ── S 系列（说话人分离）新增，非重构前遗留 ──
    "enable_speaker": False,
    "speaker_threshold": 0.5,
    "speaker_max": 8,
    "speaker_min_seg_ms": 1500,
    "speaker_max_windows": 4000,
    # ── V 系列（声纹库）新增，非重构前遗留 ──
    "enable_speaker_db": False,
    "speaker_db_path": "data/speakers.db",
    "speaker_id_threshold": 0.45,
    "speaker_id_margin": 0.10,
    "speaker_enroll_min_sec": 3.0,
    "speaker_auto_enroll": True,
    "speaker_auto_enroll_min_sec": 10.0,
    "speaker_store_audio": False,
    # ── 音频标注（Audio Tagging）新增，非重构前遗留 ──
    "enable_audio_tagging": False,
    "audio_tagging_engine": "panns",
    "audio_tagging_panns_variant": "16k",
    "audio_tagging_topk": 5,
    "audio_tagging_interval_ms": 960,
    "scene_enable": True,
    "scene_map_file": None,
    "scene_enter_sec": 2.0,
    "scene_exit_sec": 2.0,
    "scene_silence_dbfs": -50.0,
    # ── 兼容接口（/compat/*）新增，非重构前遗留 ──
    "enable_openai_api": False,
    "openai_sync_timeout": 300,
    "enable_dashscope_api": False,
    "compat_fetch_max_mb": None,
    "compat_fetch_timeout": 120,
    "compat_fetch_allow_private": False,
    "compat_external_base_url": None,
    # ── vLLM（路线 A 原生流式）新增，非重构前遗留 ──
    "gpu_memory_utilization": None,
    "vllm_max_model_len": None,
    "vllm_chunk_size_sec": None,
    "vllm_max_utterance_sec": None,
    "vllm_concurrency": None,
    "vllm_end_silence_ms": None,
    "vllm_enable_align": None,
    "vllm_align_device": None,
    "vllm_infer_batch_size": None,
    "vllm_segment_gap_ms": None,
}


def test_schema_defaults_match_legacy():
    assert schema_defaults() == LEGACY_DEFAULTS


def test_no_args_only_meta_keys():
    """未传任何参数：Namespace 仅含配置加载元参数，schema 参数全部缺席。"""
    ns = build_parser().parse_args([])
    assert vars(ns) == {"config": None, "no_config": False,
                        "update_config": False, "sync_all": False}


@pytest.mark.parametrize("spec", ARG_SPECS, ids=lambda s: s.key)
def test_each_spec_suppressed_when_absent(spec):
    """逐参数断言：未传时不出现在 Namespace（SUPPRESS 改造无遗漏）。"""
    ns = build_parser().parse_args([])
    assert not hasattr(ns, spec.attr)


@pytest.mark.parametrize("spec", ARG_SPECS, ids=lambda s: s.key)
def test_each_spec_present_when_passed(spec):
    """逐参数断言：显式传入后以正确 dest 与取值出现。"""
    if spec.type is bool:
        argv, expected = [spec.flags[0]], True
    elif spec.choices:
        argv, expected = [spec.flags[0], spec.choices[0]], spec.choices[0]
    elif spec.type is int:
        argv, expected = [spec.flags[0], "7"], 7
    elif spec.type is float:
        argv, expected = [spec.flags[0], "0.45"], 0.45
    else:
        argv, expected = [spec.flags[0], "value-x"], "value-x"
    ns = build_parser().parse_args(argv)
    assert getattr(ns, spec.attr) == expected


def test_explicit_default_value_still_present():
    """显式传默认值（--device auto）也出现在 Namespace——可覆盖配置文件（SUPPRESS 核心语义）。"""
    ns = build_parser().parse_args(["--device", "auto"])
    assert ns.device == "auto"


@pytest.mark.parametrize("flag,attr", [
    ("--no-align", "enable_align"),
    ("--no-punc", "enable_punc"),
    ("--no-web", "web"),
    ("--no-stream", "enable_stream"),
    ("--no-speaker", "enable_speaker"),
    ("--no-speaker-db", "enable_speaker_db"),
    ("--no-speaker-auto-enroll", "speaker_auto_enroll"),
    ("--no-speaker-store-audio", "speaker_store_audio"),
])
def test_negative_flags_force_false(flag, attr):
    """全部布尔开关都有反向 flag——CLI 才能把配置文件设 true 的开关覆盖回 false。"""
    ns = build_parser().parse_args([flag])
    assert getattr(ns, attr) is False


def test_bool_pair_positive_flag():
    ns = build_parser().parse_args(["--enable-align"])
    assert ns.enable_align is True


def test_use_punc_dest_compat():
    """--use-punc 的 dest 保持历史命名 enable_punc，配置文件键为 use_punc。"""
    ns = build_parser().parse_args(["--use-punc"])
    assert ns.enable_punc is True
    spec = next(s for s in ARG_SPECS if s.key == "use_punc")
    assert spec.attr == "enable_punc"


def test_invalid_choice_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--device", "tpu"])


def test_keys_and_dests_unique():
    keys = [s.key for s in ARG_SPECS]
    dests = [s.attr for s in ARG_SPECS]
    assert len(set(keys)) == len(keys)
    assert len(set(dests)) == len(dests)

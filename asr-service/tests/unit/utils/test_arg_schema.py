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
}


def test_schema_defaults_match_legacy():
    assert schema_defaults() == LEGACY_DEFAULTS


def test_no_args_only_meta_keys():
    """未传任何参数：Namespace 仅含 --config/--no-config 元参数，schema 参数全部缺席。"""
    ns = build_parser().parse_args([])
    assert vars(ns) == {"config": None, "no_config": False}


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
    else:
        argv, expected = [spec.flags[0], "value-x"], "value-x"
    ns = build_parser().parse_args(argv)
    assert getattr(ns, spec.attr) == expected


def test_explicit_default_value_still_present():
    """显式传默认值（--device auto）也出现在 Namespace——可覆盖配置文件（SUPPRESS 核心语义）。"""
    ns = build_parser().parse_args(["--device", "auto"])
    assert ns.device == "auto"


def test_bool_pair_negative_flag():
    ns = build_parser().parse_args(["--no-align"])
    assert ns.enable_align is False
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

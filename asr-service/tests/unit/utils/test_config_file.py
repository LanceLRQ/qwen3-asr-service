"""app/utils/config_file.py 测试（C2）：自动发现/引导生成/校验/四层合并优先级。"""
import argparse
import os

import pytest

import app.config as cfg
import app.utils.config_file as cf
from app.utils.arg_schema import schema_defaults


@pytest.fixture
def service_root(tmp_path, monkeypatch):
    """隔离扫描根到临时目录，并还原 cfg.CONFIG_FILE / 清空存量环境变量。"""
    monkeypatch.setattr(cf, "SERVICE_ROOT", str(tmp_path))
    monkeypatch.delenv("MODEL_SOURCE", raising=False)
    monkeypatch.delenv("ASR_API_KEY", raising=False)
    saved = cfg.CONFIG_FILE
    yield tmp_path
    cfg.CONFIG_FILE = saved


def _ns(**explicit):
    """模拟 build_parser() 输出：仅含元参数 + 本次显式给出的参数。"""
    base = {"config": None, "no_config": False}
    base.update(explicit)
    return argparse.Namespace(**base)


# ─── resolve_config_path ───

def test_no_config_short_circuits_even_if_file_exists(service_root):
    (service_root / "config.yaml").write_text("device: cpu", encoding="utf-8")
    assert cf.resolve_config_path(None, no_config=True) is None


def test_explicit_path_missing_exits(service_root):
    with pytest.raises(SystemExit, match="配置文件不存在"):
        cf.resolve_config_path(str(service_root / "nope.yaml"), no_config=False)


def test_explicit_path_used_as_is(service_root):
    p = service_root / "custom.yaml"
    p.write_text("device: cpu", encoding="utf-8")
    assert cf.resolve_config_path(str(p), no_config=False) == str(p)


def test_autodiscover_yaml(service_root):
    (service_root / "config.yaml").write_text("device: cpu", encoding="utf-8")
    assert cf.resolve_config_path(None, False) == str(service_root / "config.yaml")


def test_autodiscover_yml_alias(service_root):
    (service_root / "config.yml").write_text("device: cpu", encoding="utf-8")
    assert cf.resolve_config_path(None, False) == str(service_root / "config.yml")


def test_coexist_prefers_yaml(service_root):
    (service_root / "config.yaml").write_text("device: cpu", encoding="utf-8")
    (service_root / "config.yml").write_text("device: cuda", encoding="utf-8")
    assert cf.resolve_config_path(None, False) == str(service_root / "config.yaml")


def test_bootstrap_copies_example(service_root):
    example = service_root / "config.example.yaml"
    example.write_text("device: cpu\nweb: true\n", encoding="utf-8")
    path = cf.resolve_config_path(None, False)
    assert path == str(service_root / "config.yaml")
    assert (service_root / "config.yaml").read_text(encoding="utf-8") == example.read_text(encoding="utf-8")


def test_bootstrap_copy_failure_degrades_to_example(service_root, monkeypatch):
    example = service_root / "config.example.yaml"
    example.write_text("device: cpu\n", encoding="utf-8")

    def _fail(*a, **k):
        raise OSError("read-only fs")

    monkeypatch.setattr(cf.shutil, "copyfile", _fail)
    assert cf.resolve_config_path(None, False) == str(example)


def test_nothing_found_returns_none(service_root):
    assert cf.resolve_config_path(None, False) is None


# ─── load_config_file / validate_config ───

def _write(service_root, text):
    p = service_root / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_valid_file_maps_dest_keys(service_root):
    p = _write(service_root, "device: cpu\nuse_punc: true\nport: 9000\n")
    assert cf.load_config_file(p) == {"device": "cpu", "enable_punc": True, "port": 9000}


def test_load_empty_file_exits(service_root):
    p = _write(service_root, "")
    with pytest.raises(SystemExit, match="顶层键值映射"):
        cf.load_config_file(p)


def test_load_toplevel_list_exits(service_root):
    p = _write(service_root, "- a\n- b\n")
    with pytest.raises(SystemExit, match="顶层键值映射"):
        cf.load_config_file(p)


def test_load_broken_yaml_exits(service_root):
    p = _write(service_root, "device: [unclosed\n")
    with pytest.raises(SystemExit, match="解析失败"):
        cf.load_config_file(p)


def test_unknown_key_exits_with_hint(service_root):
    with pytest.raises(SystemExit, match=r"未知配置键: divice（是否想写 device？）"):
        cf.validate_config({"divice": "cpu"})


def test_null_value_exits(service_root):
    with pytest.raises(SystemExit, match="值为空"):
        cf.validate_config({"model_size": None})


@pytest.mark.parametrize("data,msg", [
    ({"web": "yes"}, "期望 true/false"),
    ({"port": "8765"}, "期望整数"),
    ({"port": True}, "期望整数"),          # YAML bool 不得冒充 int
    ({"host": 123}, "期望字符串"),
    ({"device": "tpu"}, "非法取值"),
])
def test_type_and_choices_validation(service_root, data, msg):
    with pytest.raises(SystemExit, match=msg):
        cf.validate_config(data)


def test_errors_are_aggregated(service_root):
    """多处错误一次性全部报出，不挤牙膏。"""
    with pytest.raises(SystemExit) as ei:
        cf.validate_config({"divice": "cpu", "port": "x", "device": "tpu"})
    text = str(ei.value)
    assert "divice" in text and "port" in text and "device" in text


# ─── merge_runtime_config 四层优先级 ───

def test_merge_defaults_only(service_root):
    merged = cf.merge_runtime_config(_ns(no_config=True))
    assert vars(merged) == schema_defaults()
    assert cfg.CONFIG_FILE is None


def test_merge_env_over_defaults(service_root, monkeypatch):
    monkeypatch.setenv("MODEL_SOURCE", "huggingface")
    monkeypatch.setenv("ASR_API_KEY", "env-secret")
    merged = cf.merge_runtime_config(_ns(no_config=True))
    assert merged.model_source == "huggingface"
    assert merged.api_key == "env-secret"


def test_merge_file_over_env(service_root, monkeypatch):
    monkeypatch.setenv("MODEL_SOURCE", "huggingface")
    monkeypatch.setenv("ASR_API_KEY", "env-secret")
    _write(service_root, 'model_source: modelscope\napi_key: ""\n')
    merged = cf.merge_runtime_config(_ns())
    assert merged.model_source == "modelscope"
    assert merged.api_key == ""
    assert cfg.CONFIG_FILE == "config.yaml"


def test_merge_cli_over_file(service_root):
    _write(service_root, "device: cpu\nport: 9000\n")
    merged = cf.merge_runtime_config(_ns(device="cuda"))
    assert merged.device == "cuda"      # CLI 显式最高
    assert merged.port == 9000          # 未被 CLI 覆盖的文件值保留


def test_merge_cli_explicit_default_over_file(service_root):
    """显式传默认值（--device auto）也能覆盖文件值——SUPPRESS 语义验证。"""
    _write(service_root, "device: cpu\n")
    merged = cf.merge_runtime_config(_ns(device="auto"))
    assert merged.device == "auto"


def test_merge_records_config_file_basename(service_root):
    p = service_root / "custom.yml"
    p.write_text("device: cpu\n", encoding="utf-8")
    cf.merge_runtime_config(_ns(config=str(p)))
    assert cfg.CONFIG_FILE == "custom.yml"


# ─── example 一致性（防示例与 schema 漂移）───

def test_example_passes_schema_validation():
    example = os.path.join(cfg.BASE_DIR, "config.example.yaml")
    parsed = cf.load_config_file(example)
    # 评审定稿的关键默认值组合（2026-06-04）
    assert parsed["device"] == "cuda"
    assert parsed["host"] == "127.0.0.1"
    assert parsed["model_size"] == "0.6b"
    assert parsed["enable_align"] is False
    assert parsed["enable_stream"] is True
    assert parsed["web"] is True
    assert parsed["api_key"] == ""

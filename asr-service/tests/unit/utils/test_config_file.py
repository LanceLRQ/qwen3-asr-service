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
    # 生成文件后续可能写入 api_key，必须仅属主可读写
    assert (service_root / "config.yaml").stat().st_mode & 0o777 == 0o600


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


def test_duplicate_key_exits(service_root):
    """YAML 规范默认重复键末值静默胜出——本服务按坏文件硬报错处理。"""
    p = _write(service_root, "device: cpu\nport: 9000\ndevice: cuda\n")
    with pytest.raises(SystemExit, match="重复的配置键: device"):
        cf.load_config_file(p)


def test_type_error_includes_choices_hint(service_root):
    """最常见笔误 model_size: 1.7（YAML 浮点）应提示合法值，而非干巴巴的类型报错。"""
    with pytest.raises(SystemExit, match=r"model_size: 期望字符串.*可选 0\.6b \| 1\.7b"):
        cf.validate_config({"model_size": 1.7})


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


def test_merge_cli_negative_bool_over_file(service_root):
    """反向 flag（--no-stream 等）使 CLI 能把文件设 true 的布尔开关覆盖回 false。"""
    _write(service_root, "enable_stream: true\nweb: true\n")
    merged = cf.merge_runtime_config(_ns(enable_stream=False))
    assert merged.enable_stream is False    # CLI 显式 false 胜过文件 true
    assert merged.web is True               # 未覆盖的文件值保留


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
    assert parsed["device"] == "auto"   # 自动检测：无 GPU 回退 CPU，避免首次生成 config 即写死 cuda 崩溃
    assert parsed["host"] == "127.0.0.1"
    assert parsed["model_size"] == "0.6b"
    assert parsed["enable_align"] is False
    assert parsed["enable_stream"] is True
    assert parsed["web"] is True
    assert parsed["api_key"] == ""
    assert parsed["enable_task_store"] is True   # P 系列：example 默认开启（schema 默认关闭）
    assert parsed["enable_speaker"] is False     # S 系列：example 默认关闭（与 schema 一致）
    assert parsed["enable_speaker_db"] is False  # V 系列：example 默认关闭（依赖 api_key）


# ─── float 类型（S 系列 speaker_threshold）───

def test_float_accepts_float_value(service_root):
    out = cf.validate_config({"speaker_threshold": 0.45})
    assert out["speaker_threshold"] == 0.45


def test_float_accepts_int_and_normalizes(service_root):
    out = cf.validate_config({"speaker_threshold": 1})
    assert out["speaker_threshold"] == 1.0
    assert isinstance(out["speaker_threshold"], float)


def test_float_rejects_bool(service_root):
    with pytest.raises(SystemExit, match="speaker_threshold: 期望数值"):
        cf.validate_config({"speaker_threshold": True})


def test_float_rejects_str(service_root):
    with pytest.raises(SystemExit, match="speaker_threshold: 期望数值"):
        cf.validate_config({"speaker_threshold": "0.5"})


# ─── sync_config_with_example（自动补全 example 新增项）───

def _ex(service_root, text):
    p = service_root / cf.EXAMPLE_NAME
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_sync_appends_missing_active_keys(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "device: cuda\nenable_stream: true\n# max_queue_size: 100\n")
    added = cf.sync_config_with_example(c, e)
    assert added == ["enable_stream"]                 # device 已存在；max_queue_size 在 example 注释→不取
    text = (service_root / "config.yaml").read_text(encoding="utf-8")
    assert "device: cpu" in text                      # 既有值保留
    assert "enable_stream: true" in text              # 新增项以 example 值追加


def test_sync_strips_inline_comment(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "use_punc: false  # 标点恢复\n")
    cf.sync_config_with_example(c, e)
    text = (service_root / "config.yaml").read_text(encoding="utf-8")
    assert "use_punc: false" in text          # 值保留
    assert "# 标点恢复" not in text            # 行内注释剥离，保持 config 简洁


def test_sync_skips_key_commented_in_config(service_root):
    c = _write(service_root, "# enable_stream: true\n")   # 用户主动注释=已声明
    e = _ex(service_root, "enable_stream: true\n")
    assert cf.sync_config_with_example(c, e) == []


def test_sync_skips_unknown_key(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "device: cuda\nbogus_key: 1\n")
    assert cf.sync_config_with_example(c, e) == []    # bogus_key 不在 schema，不引入


def test_sync_idempotent(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "device: cuda\nenable_stream: true\n")
    assert cf.sync_config_with_example(c, e) == ["enable_stream"]
    assert cf.sync_config_with_example(c, e) == []    # 二次无新增（幂等）


def test_sync_default_skips_commented_advanced(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "device: cuda\n# max_queue_size: 100  # 队列\n")
    assert cf.sync_config_with_example(c, e) == []    # 默认仅同步推荐（激活）项


def test_sync_all_includes_commented_advanced(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "device: cuda\n# max_queue_size: 100  # 队列\n")
    assert cf.sync_config_with_example(c, e, include_all=True) == ["max_queue_size"]
    text = (service_root / "config.yaml").read_text(encoding="utf-8")
    assert "# max_queue_size: 100" in text            # 注释态补入（禁用+默认值引用）
    assert "# 队列" not in text                       # 行内注释剥离
    # 二次（--all）幂等：已声明（注释态）不再补
    assert cf.sync_config_with_example(c, e, include_all=True) == []


def test_sync_all_idempotent_against_default(service_root):
    c = _write(service_root, "device: cpu\n")
    e = _ex(service_root, "device: cuda\n# max_queue_size: 100\n")
    cf.sync_config_with_example(c, e, include_all=True)   # 注释态补入 max_queue_size
    assert cf.sync_config_with_example(c, e) == []        # 默认轮也视其为"已有"，不激活覆盖


# ─── run_config_update（--update-config：仅更新文件，不启动服务）───

def test_update_config_syncs_and_returns_added(service_root):
    _write(service_root, "device: cpu\n")
    _ex(service_root, "device: cuda\nuse_punc: true\n")
    added = cf.run_config_update(None, no_config=False)
    assert added == ["use_punc"]
    assert "use_punc: true" in (service_root / "config.yaml").read_text(encoding="utf-8")


def test_update_config_bootstraps_when_no_local(service_root):
    _ex(service_root, "device: cuda\nuse_punc: true\n")        # 仅有 example，无本地配置
    assert cf.run_config_update(None, no_config=False) == []   # 引导生成即最新，无"新增"
    gen = service_root / "config.yaml"
    assert gen.is_file()
    assert gen.read_text(encoding="utf-8") == (service_root / cf.EXAMPLE_NAME).read_text(encoding="utf-8")
    assert gen.stat().st_mode & 0o777 == 0o600


def test_update_config_targets_explicit_config_arg(service_root):
    p = service_root / "custom.yaml"
    p.write_text("device: cpu\n", encoding="utf-8")
    _ex(service_root, "device: cuda\nuse_punc: true\n")
    assert cf.run_config_update(str(p), no_config=False) == ["use_punc"]
    assert "use_punc: true" in p.read_text(encoding="utf-8")


def test_update_config_all_syncs_advanced(service_root):
    _write(service_root, "device: cpu\n")
    _ex(service_root, "device: cuda\nuse_punc: false\n# max_queue_size: 100\n")
    # 默认仅补推荐项（use_punc）；高级注释项 max_queue_size 不补
    assert cf.run_config_update(None, no_config=False) == ["use_punc"]
    # --all 再补高级项（注释态）
    assert cf.run_config_update(None, no_config=False, include_all=True) == ["max_queue_size"]
    assert "# max_queue_size: 100" in (service_root / "config.yaml").read_text(encoding="utf-8")


def test_update_config_conflicts_with_no_config(service_root):
    _ex(service_root, "device: cuda\n")
    with pytest.raises(SystemExit, match="互斥"):
        cf.run_config_update(None, no_config=True)


def test_update_config_missing_example_exits(service_root):
    with pytest.raises(SystemExit, match="缺失"):
        cf.run_config_update(None, no_config=False)


def test_autodiscover_no_sync_by_default(service_root):
    _write(service_root, "device: cpu\n")
    _ex(service_root, "device: cuda\nuse_punc: true\n")
    cf.resolve_config_path(None, no_config=False)     # 启动加载路径不再产生同步副作用
    assert "use_punc: true" not in (service_root / "config.yaml").read_text(encoding="utf-8")

"""YAML 配置文件：自动发现 / 引导生成 / 校验 / 优先级合并。

优先级链（低→高）：schema 默认值 < 环境变量(ASR_API_KEY/MODEL_SOURCE) < 配置文件 < CLI 显式参数。
仅支持 YAML（config.yaml / config.yml）；键名 = CLI 长参数横线转下划线，扁平结构。
设计文档：docs/plan/features/config_file/config-file-design.md §3
"""
import argparse
import difflib
import logging
import os
import shutil

import yaml

import app.config as cfg
from app.utils.arg_schema import ARG_SPECS, schema_defaults

logger = logging.getLogger(__name__)

# 扫描根 = 服务根目录（start.sh 已 cd 至此），不扫调用者任意 cwd
SERVICE_ROOT = cfg.BASE_DIR
EXAMPLE_NAME = "config.example.yaml"


def resolve_config_path(cli_value: str | None, no_config: bool) -> str | None:
    """--no-config 短路；--config 显式优先；否则自动发现，未命中时由 example 引导生成。"""
    if no_config:
        return None
    if cli_value is not None:
        if not os.path.isfile(cli_value):
            raise SystemExit(f"配置文件不存在: {cli_value}")
        return cli_value

    yaml_path = os.path.join(SERVICE_ROOT, "config.yaml")
    yml_path = os.path.join(SERVICE_ROOT, "config.yml")
    if os.path.isfile(yaml_path):
        if os.path.isfile(yml_path):
            logger.warning("config.yaml 与 config.yml 并存，已加载 config.yaml，忽略 config.yml")
        logger.info("自动加载本地配置: config.yaml")
        return yaml_path
    if os.path.isfile(yml_path):
        logger.info("自动加载本地配置: config.yml")
        return yml_path

    example = os.path.join(SERVICE_ROOT, EXAMPLE_NAME)
    if os.path.isfile(example):
        try:
            shutil.copyfile(example, yaml_path)   # 引导生成：首启即获得可编辑的真实配置
        except OSError as e:
            logger.warning(f"config.yaml 生成失败（{e}），本次直接加载 {EXAMPLE_NAME}")
            return example
        logger.info(f"未发现 config.yaml，已从 {EXAMPLE_NAME} 生成默认配置")
        return yaml_path
    logger.warning(f"{EXAMPLE_NAME} 缺失，按内置默认值启动")
    return None


def load_config_file(path: str) -> dict:
    """YAML 解析 + schema 校验，返回 dest 键的扁平 dict。任何错误 SystemExit 带可读信息。

    显式指定与自动发现一视同仁：文件存在即意图明确，坏文件硬报错而非静默跳过。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise SystemExit(f"配置文件无法读取: {path}: {e}")
    except yaml.YAMLError as e:
        raise SystemExit(f"配置文件解析失败: {path}: {e}")
    if not isinstance(data, dict):
        raise SystemExit(f"配置文件须为顶层键值映射（空文件/列表/标量均不接受）: {path}")
    return validate_config(data, source=path)


def validate_config(data: dict, source: str = "<config>") -> dict:
    """逐键校验：未知键（带近似提示）/ 空值 / 类型 / choices；通过则返回 dest 键 dict。"""
    specs_by_key = {spec.key: spec for spec in ARG_SPECS}
    errors = []
    result = {}
    for key, value in data.items():
        spec = specs_by_key.get(key)
        if spec is None:
            close = difflib.get_close_matches(str(key), specs_by_key, n=1)
            hint = f"（是否想写 {close[0]}？）" if close else ""
            errors.append(f"未知配置键: {key}{hint}")
            continue
        if value is None:
            errors.append(f"{key}: 值为空（如需使用默认值请删除该键）")
            continue
        if spec.type is bool:
            if not isinstance(value, bool):
                errors.append(f"{key}: 期望 true/false，实得 {value!r}")
                continue
        elif spec.type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{key}: 期望整数，实得 {value!r}")
                continue
        else:
            if not isinstance(value, str):
                errors.append(f"{key}: 期望字符串，实得 {value!r}")
                continue
        if spec.choices and value not in spec.choices:
            errors.append(f"{key}: 非法取值 {value!r}，可选 {' | '.join(spec.choices)}")
            continue
        result[spec.attr] = value
    if errors:
        raise SystemExit(f"配置文件校验失败: {source}\n  - " + "\n  - ".join(errors))
    return result


def merge_runtime_config(cli_ns: argparse.Namespace) -> argparse.Namespace:
    """四层合并：schema 默认值 ← 环境变量 ← 配置文件 ← CLI 显式参数。

    cli_ns 来自 build_parser()（全 SUPPRESS），仅含本次显式给出的参数；
    生效配置文件名记入 cfg.CONFIG_FILE（/health 回显，防"幽灵配置"）。
    """
    cli = dict(vars(cli_ns))
    no_config = cli.pop("no_config", False)
    config_arg = cli.pop("config", None)

    merged = schema_defaults()

    # ② 环境变量（存量两项，空值视为未设置）
    env_model_source = os.environ.get("MODEL_SOURCE")
    if env_model_source:
        merged["model_source"] = env_model_source
    env_api_key = os.environ.get("ASR_API_KEY")
    if env_api_key:
        merged["api_key"] = env_api_key

    # ③ 配置文件
    path = resolve_config_path(config_arg, no_config)
    if path is not None:
        merged.update(load_config_file(path))
    cfg.CONFIG_FILE = os.path.basename(path) if path else None

    # ④ CLI 显式参数（最高，含"显式传默认值"）
    merged.update(cli)
    return argparse.Namespace(**merged)

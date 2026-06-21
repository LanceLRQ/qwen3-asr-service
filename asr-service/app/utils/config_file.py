"""YAML 配置文件：自动发现 / 引导生成 / 校验 / 优先级合并。

优先级链（低→高）：schema 默认值 < 环境变量(ASR_API_KEY/MODEL_SOURCE) < 配置文件 < CLI 显式参数。
仅支持 YAML（config.yaml / config.yml）；键名 = CLI 长参数横线转下划线，扁平结构。
设计文档：docs/plan/features/20260604_config_file/config-file-design.md §3
"""
import argparse
import difflib
import logging
import os
import re
import shutil
from collections.abc import Hashable

import yaml

import app.config as cfg
from app.utils.arg_schema import ARG_SPECS, schema_defaults

logger = logging.getLogger(__name__)

# 扫描根 = 服务根目录（start.sh 已 cd 至此），不扫调用者任意 cwd
SERVICE_ROOT = cfg.BASE_DIR
EXAMPLE_NAME = "config.example.yaml"

# 顶层键（无缩进；可前缀 # 表示注释项）。配置为扁平结构，按行提取即可
_TOP_KEY_RE = re.compile(r"^(#\s*)?([A-Za-z_]\w*)\s*:")


def _mentioned_keys(text: str) -> set:
    """文件中出现过的顶层键（含被注释的），用于判断"是否已存在"。"""
    keys = set()
    for line in text.splitlines():
        if line[:1].isspace():            # 有缩进 → 非顶层，跳过
            continue
        m = _TOP_KEY_RE.match(line)
        if m:
            keys.add(m.group(2))
    return keys


def _example_entries(text: str, include_all: bool = False) -> list:
    """example 顶层项 → [(key, 源行)]，作为待补全候选。

    include_all=False：仅"激活（未注释）"项（推荐项）；
    include_all=True：连注释态的高级/可选项一并纳入（追加时保持其注释态）。
    """
    out = []
    for line in text.splitlines():
        if line[:1].isspace():
            continue
        m = _TOP_KEY_RE.match(line)
        if not m:
            continue
        if m.group(1) is None or include_all:   # 激活项总取；注释项仅 include_all 取
            out.append((m.group(2), line.rstrip()))
    return out


def _strip_inline_comment(line: str) -> str:
    """去掉行内注释（值后的 # 说明），保留注释态条目行首的注释开关 '#'，保持 config 简洁。

    从键名处起扫描，跳过行首开关；引号内的 # 不视为注释。值内无裸 # 的前提下安全。
    """
    s = line.rstrip()
    m = _TOP_KEY_RE.match(s)
    start = m.start(2) if m else 0           # 从 key 起始扫描，避开行首注释开关
    in_s = in_d = False
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and s[i - 1].isspace():
            return s[:i].rstrip()
    return s


def sync_config_with_example(config_path: str, example_path: str, *,
                             include_all: bool = False) -> list:
    """把 example 中存在、config 中缺失（且 schema 合法）的项追加进 config，只补不覆盖；
    返回新增的键列表。非破坏性、幂等、失败仅告警。

    include_all=False：仅同步推荐项（example 激活项）；
    include_all=True：连高级/可选项（example 注释项）也补，保持其注释态（禁用+默认值引用）。
    追加行去掉行内注释、不加额外标记，保持 config.yaml 简洁。
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            config_text = f.read()
        with open(example_path, encoding="utf-8") as f:
            example_text = f.read()
    except OSError as e:
        logger.warning(f"配置同步跳过（读取失败）: {e}")
        return []

    have = _mentioned_keys(config_text)
    valid = {spec.key for spec in ARG_SPECS}    # 防 example/schema 漂移引入非法键
    missing = [(k, line) for k, line in _example_entries(example_text, include_all)
               if k not in have and k in valid]
    if not missing:
        return []

    block = "\n".join(_strip_inline_comment(line) for _, line in missing)
    new_text = config_text.rstrip("\n") + "\n\n" + block + "\n"
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError as e:
        logger.warning(f"配置同步写入失败: {e}")
        return []

    added = [k for k, _ in missing]
    logger.info(f"配置同步：已向 {os.path.basename(config_path)} 追加 "
                f"{len(added)} 个缺失项: {', '.join(added)}")
    return added


def run_config_update(config_arg: str | None, no_config: bool,
                      include_all: bool = False) -> list:
    """--update-config：把 config.example.yaml 缺失的项同步进本地 config.yaml
    （未发现则由 example 引导生成），返回新增键列表。仅更新文件——调用方据此退出，
    不加载校验、不启动服务。

    include_all=True（--all）：连高级/可选项一并同步（注释态补入）；默认仅同步推荐项。
    """
    if no_config:
        raise SystemExit("--update-config 与 --no-config 互斥")
    example = os.path.join(SERVICE_ROOT, EXAMPLE_NAME)
    if not os.path.isfile(example):
        raise SystemExit(f"{EXAMPLE_NAME} 缺失，无法更新配置")

    # 目标文件：--config 显式优先 > 自动发现 config.yaml > config.yml
    if config_arg is not None:
        if not os.path.isfile(config_arg):
            raise SystemExit(f"配置文件不存在: {config_arg}")
        target = config_arg
    else:
        yaml_path = os.path.join(SERVICE_ROOT, "config.yaml")
        yml_path = os.path.join(SERVICE_ROOT, "config.yml")
        if os.path.isfile(yaml_path):
            target = yaml_path
        elif os.path.isfile(yml_path):
            target = yml_path
        else:
            shutil.copyfile(example, yaml_path)        # 无本地配置 → 引导生成即为最新
            os.chmod(yaml_path, 0o600)                 # 后续可能写入 api_key，收紧权限
            logger.info(f"未发现 config.yaml，已从 {EXAMPLE_NAME} 生成默认配置")
            return []

    added = sync_config_with_example(target, example, include_all=include_all)
    name = os.path.basename(target)
    if added:
        logger.info(f"已更新 {name}：新增 {len(added)} 项（{', '.join(added)}）")
    else:
        scope = "全部" if include_all else "推荐"
        logger.info(f"{name} 已含全部{scope}项，无需更新（高级项可加 --all 同步）"
                    if not include_all else f"{name} 已是最新，无新增项")
    return added


def resolve_config_path(cli_value: str | None, no_config: bool) -> str | None:
    """--no-config 短路；--config 显式优先；否则自动发现，未命中时由 example 引导生成。

    仅做发现/引导生成，不改动既有配置内容；example 新增项的同步是 --update-config 的
    独立动作（见 run_config_update），不在启动加载路径上产生副作用。
    """
    if no_config:
        return None
    if cli_value is not None:
        if not os.path.isfile(cli_value):
            raise SystemExit(f"配置文件不存在: {cli_value}")
        return cli_value

    yaml_path = os.path.join(SERVICE_ROOT, "config.yaml")
    yml_path = os.path.join(SERVICE_ROOT, "config.yml")
    example = os.path.join(SERVICE_ROOT, EXAMPLE_NAME)
    if os.path.isfile(yaml_path):
        if os.path.isfile(yml_path):
            logger.warning("config.yaml 与 config.yml 并存，已加载 config.yaml，忽略 config.yml")
        logger.info("自动加载本地配置: config.yaml")
        return yaml_path
    if os.path.isfile(yml_path):
        logger.info("自动加载本地配置: config.yml")
        return yml_path

    if os.path.isfile(example):
        try:
            shutil.copyfile(example, yaml_path)   # 引导生成：首启即获得可编辑的真实配置
            os.chmod(yaml_path, 0o600)            # 该文件后续可能写入 api_key，收紧为仅属主可读写
        except OSError as e:
            logger.warning(f"config.yaml 生成失败（{e}），本次直接加载 {EXAMPLE_NAME}")
            return example
        logger.info(f"未发现 config.yaml，已从 {EXAMPLE_NAME} 生成默认配置")
        return yaml_path
    logger.warning(f"{EXAMPLE_NAME} 缺失，按内置默认值启动")
    return None


class _UniqueKeyLoader(yaml.SafeLoader):
    """重复键硬报错的 SafeLoader——YAML 规范默认末值静默胜出，
    会掩盖配置文件中的拼写残留/合并事故，与"未知键硬报错"同一防线。"""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if isinstance(key, Hashable):     # 不可哈希键交由父类按 YAML 规范报错
                if key in seen:
                    raise yaml.YAMLError(f"重复的配置键: {key}（第 {key_node.start_mark.line + 1} 行）")
                seen.add(key)
        return super().construct_mapping(node, deep)


def load_config_file(path: str) -> dict:
    """YAML 解析 + schema 校验，返回 dest 键的扁平 dict。任何错误 SystemExit 带可读信息。

    显式指定与自动发现一视同仁：文件存在即意图明确，坏文件硬报错而非静默跳过。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=_UniqueKeyLoader)
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
        # 类型错误时附带合法值提示——最常见的笔误（如 model_size: 1.7 漏写 b 被 YAML
        # 解析为浮点）应得到"可选 0.6b | 1.7b"而非干巴巴的类型报错
        choices_hint = f"（可选 {' | '.join(spec.choices)}）" if spec.choices else ""
        if spec.type is bool:
            if not isinstance(value, bool):
                errors.append(f"{key}: 期望 true/false，实得 {value!r}{choices_hint}")
                continue
        elif spec.type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{key}: 期望整数，实得 {value!r}{choices_hint}")
                continue
        elif spec.type is float:
            # int 写法（如 speaker_threshold: 1）同样接受，统一归一化为 float
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{key}: 期望数值，实得 {value!r}{choices_hint}")
                continue
            value = float(value)
        elif spec.type is dict:
            if not isinstance(value, dict) or not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) for v in value.values()):
                errors.append(f"{key}: 期望 dict[str, 数值]，实得 {value!r}")
                continue
            value = {str(k): float(v) for k, v in value.items()}
        else:
            if not isinstance(value, str):
                errors.append(f"{key}: 期望字符串，实得 {value!r}{choices_hint}")
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
    cli.pop("update_config", None)   # --update-config 是独立的"更新即退出"动作，不参与启动合并
    cli.pop("sync_all", None)        # --all 仅配合 --update-config，不参与启动合并
    cli.pop("help_lang", None)  # --lang 仅决定 --help 文案语言，非运行配置，剔除

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

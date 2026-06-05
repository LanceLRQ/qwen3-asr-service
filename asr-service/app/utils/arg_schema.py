"""启动参数单一 schema：同一份定义驱动 argparse、配置文件校验与 config.example.yaml。

设计要点（docs/plan/features/config_file/config-file-design.md §3.2）：
- argparse 的 default 一律 SUPPRESS——否则无法区分"显式传了默认值"与"没传"，
  配置文件的覆盖语义（默认值 < 环境变量 < 配置文件 < CLI 显式参数）会错；
- 实义默认值收敛到本表（schema_defaults），消除 CLI 定义 / 文件校验 / 示例文件三处漂移；
- key = 配置文件键名（CLI 长参数横线转下划线）；dest = argparse Namespace 属性名，
  仅 --use-punc 二者不同（历史 dest=enable_punc，保持兼容）。
"""
import argparse
from dataclasses import dataclass

import app.config as cfg


@dataclass(frozen=True)
class ArgSpec:
    key: str                      # 配置文件键名（= CLI 长参数横线转下划线）
    flags: tuple                  # CLI flag（bool 型为"开启" flag）
    default: object = None        # 实义默认值（argparse 一律 SUPPRESS）
    type: type = str              # str / int / float / bool（bool 走 store_true/store_false）
    choices: tuple = None
    help: str = ""
    dest: str = None              # argparse dest，缺省 = key
    negative_flags: tuple = ()    # bool 开关对的"关闭" flag（如 --no-align）
    negative_help: str = ""
    group: str = "其他"           # 生效配置打印的分组，随定义声明（勿留在"其他"）

    @property
    def attr(self) -> str:
        return self.dest or self.key


ARG_SPECS = (
    ArgSpec(
        key="serve_mode", flags=("--serve-mode",), default="standard",
        choices=("standard", "vllm"), group="服务",
        help="服务运行模式：standard=transformers/OpenVINO 离线+实时(路线B)；"
             "vllm=vLLM 原生流式(Phase 3) (default: standard)",
    ),
    ArgSpec(
        key="device", flags=("--device",), default="auto",
        choices=("auto", "cuda", "cpu"), group="模型",
        help="运行设备 (default: auto)",
    ),
    ArgSpec(
        key="model_size", flags=("--model-size",), default=None,
        choices=("0.6b", "1.7b"), group="模型",
        help="ASR 模型大小 (default: 根据显存自动选择)",
    ),
    ArgSpec(
        key="enable_align", flags=("--enable-align",), default=True, type=bool,
        group="模型",
        help="加载对齐模型 (default)",
        negative_flags=("--no-align",), negative_help="不加载对齐模型",
    ),
    ArgSpec(
        key="use_punc", flags=("--use-punc",), default=False, type=bool,
        dest="enable_punc", group="模型",
        help="启用标点恢复",
        negative_flags=("--no-punc",), negative_help="禁用标点恢复（覆盖配置文件）",
    ),
    ArgSpec(
        key="model_source", flags=("--model-source",), default="modelscope",
        choices=("modelscope", "huggingface"), group="模型",
        help="模型下载源 (default: modelscope)",
    ),
    ArgSpec(
        key="host", flags=("--host",), default=None, group="服务",
        help="监听地址 (default: 127.0.0.1)",
    ),
    ArgSpec(
        key="port", flags=("--port",), default=None, type=int, group="服务",
        help="监听端口 (default: 8765)",
    ),
    ArgSpec(
        key="web", flags=("--web",), default=False, type=bool, group="服务",
        help="启用 Web UI (访问 /web-ui)",
        negative_flags=("--no-web",), negative_help="禁用 Web UI（覆盖配置文件）",
    ),
    ArgSpec(
        key="max_segment", flags=("--max-segment",), default=5, type=int,
        group="离线任务",
        help="VAD 切片合并最大时长，单位秒 (default: 5)",
    ),
    ArgSpec(
        key="api_key", flags=("--api-key",), default=None, group="服务",
        help="API 密钥，设置后启用 Bearer token 认证（覆盖 ASR_API_KEY 环境变量）",
    ),
    ArgSpec(
        key="max_queue_size", flags=("--max-queue-size",), default=None, type=int,
        group="离线任务",
        help=f"任务队列最大长度 (default: {cfg.MAX_QUEUE_SIZE})",
    ),
    ArgSpec(
        key="enable_stream", flags=("--enable-stream",), default=False, type=bool,
        group="实时转写",
        help="挂载实时转写端点 WS /v2/asr/stream（路线B，standard 模式）",
        negative_flags=("--no-stream",), negative_help="不挂载实时转写端点（覆盖配置文件）",
    ),
    ArgSpec(
        key="max_stream_sessions", flags=("--max-stream-sessions",), default=None, type=int,
        group="实时转写",
        help=f"实时最大并发会话数 (default: {cfg.MAX_STREAM_SESSIONS})",
    ),
    ArgSpec(
        key="stream_asr_concurrency", flags=("--stream-asr-concurrency",), default=None, type=int,
        group="实时转写",
        help=f"实时 ASR 解码并发上限 (default: {cfg.STREAM_ASR_CONCURRENCY})",
    ),
    ArgSpec(
        key="enable_task_store", flags=("--enable-task-store",), default=False, type=bool,
        group="离线任务",
        help="离线任务持久化（data/tasks.db）：结果跨重启可查",
        negative_flags=("--no-task-store",), negative_help="关闭任务持久化（覆盖配置文件）",
    ),
    ArgSpec(
        key="task_db_path", flags=("--task-db-path",), default="data/tasks.db",
        group="离线任务",
        help="任务库路径，相对服务根目录 (default: data/tasks.db)",
    ),
    ArgSpec(
        key="task_retention_days", flags=("--task-retention-days",), default=7, type=int,
        group="离线任务",
        help="过期任务清理窗口（天），启动时执行；0=永不清理 (default: 7)",
    ),
    ArgSpec(
        key="enable_speaker", flags=("--enable-speaker",), default=False, type=bool,
        group="说话人分离",
        help="说话人分离：离线 segment.speaker / 实时 final.speaker（匿名 A/B/C…）",
        negative_flags=("--no-speaker",), negative_help="关闭说话人分离（覆盖配置文件）",
    ),
    ArgSpec(
        key="speaker_threshold", flags=("--speaker-threshold",), default=0.5, type=float,
        group="说话人分离",
        help="实时在线归簇余弦阈值，实测可用区间 [0.35, 0.65] (default: 0.5)",
    ),
    ArgSpec(
        key="speaker_max", flags=("--speaker-max",), default=8, type=int,
        group="说话人分离",
        help="说话人数上限：实时硬上限，离线谱聚类簇数搜索上界 (default: 8)",
    ),
    ArgSpec(
        key="speaker_min_seg_ms", flags=("--speaker-min-seg-ms",), default=1500, type=int,
        group="说话人分离",
        help="实时短段门槛（毫秒）：短于此不建新簇/不更新质心 (default: 1500)",
    ),
    ArgSpec(
        key="speaker_max_windows", flags=("--speaker-max-windows",), default=4000, type=int,
        group="说话人分离",
        help="离线滑窗数上限，超出均匀抽稀（超长音频内存防护） (default: 4000)",
    ),
)


def build_parser() -> argparse.ArgumentParser:
    """由 schema 生成 argparse：全参数 default=SUPPRESS（未传则不出现在 Namespace）。

    --config / --no-config 为配置加载的元参数，不属于 schema（不能写进配置文件），
    单独注册并保留普通 default。
    """
    parser = argparse.ArgumentParser(description="Qwen3-ASR Service")
    for spec in ARG_SPECS:
        if spec.type is bool:
            parser.add_argument(
                *spec.flags, dest=spec.attr, action="store_true",
                default=argparse.SUPPRESS, help=spec.help,
            )
            if spec.negative_flags:
                parser.add_argument(
                    *spec.negative_flags, dest=spec.attr, action="store_false",
                    default=argparse.SUPPRESS, help=spec.negative_help,
                )
        else:
            kwargs = dict(dest=spec.attr, default=argparse.SUPPRESS, help=spec.help)
            if spec.type is not str:
                kwargs["type"] = spec.type
            if spec.choices:
                kwargs["choices"] = spec.choices
            parser.add_argument(*spec.flags, **kwargs)

    group = parser.add_argument_group("配置文件")
    group.add_argument(
        "--config", dest="config", default=None, metavar="PATH",
        help="YAML 配置文件路径（缺省时自动发现 config.yaml/config.yml，"
             "未发现则由 config.example.yaml 引导生成）",
    )
    group.add_argument(
        "--no-config", dest="no_config", action="store_true", default=False,
        help="跳过配置文件加载与引导生成（纯默认值+环境变量+CLI 启动）",
    )
    return parser


def schema_defaults() -> dict:
    """实义默认值表（dest 键），优先级链的第①层。"""
    return {spec.attr: spec.default for spec in ARG_SPECS}

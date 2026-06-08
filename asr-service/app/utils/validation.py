"""会话/请求级可覆盖参数的类型转换 + 范围钳制（WS start 与离线 Form 共用）。

策略：
- 参数本身非法（类型错 / 越界）→ 抛 ValueError（调用方据此回 invalid_config / 400）。
- 参数合法但服务端未启用对应功能 → 不在此处理，由调用方收集为 warnings 软提示。
"""

# ─── 可覆盖参数的服务端硬边界（防滥用）───
SPK_THRESHOLD_RANGE = (0.2, 0.9)        # 在线归簇余弦阈值（实测可用 [0.35,0.65]，放宽留余量）
SPK_MIN_SEG_RANGE = (0, 10000)          # ms，短段门槛
SPK_MAX_RANGE = (1, 50)                 # 说话人上限（资源界）
MAX_SEGMENT_SEC_RANGE = (1, 60)         # s，实时长句兜底切分
MAX_SEGMENT_RANGE = (1, 30)             # s，离线 VAD 切片合并
MAX_END_SILENCE_RANGE = (200, 2000)     # ms，断句尾静音
SPK_ID_THRESHOLD_RANGE = (0.0, 1.0)     # 1:N 开集识别阈
SPK_ID_MARGIN_RANGE = (0.0, 1.0)        # top1-top2 margin
ENERGY_FLOOR_RANGE = (-90.0, 0.0)       # dBFS（满量程参考，≤0）
SNR_MIN_RANGE = (0.0, 40.0)             # dB；0=关闭 SNR 门


def coerce_num_in_range(value, rng, name, *, cast=float):
    """数值参数：类型转换 + 范围校验，越界/非数值抛 ValueError（布尔不视为数值）。"""
    lo, hi = rng
    if isinstance(value, bool):
        raise ValueError(f"{name} 非法: {value!r}")
    try:
        v = cast(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 非法: {value!r}")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} 必须在 [{lo}, {hi}] 范围内，收到 {v}")
    return v


def parse_bool(value, default, name):
    """布尔开关：None=沿用默认；非布尔抛 ValueError。"""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须为布尔值，收到 {value!r}")
    return value

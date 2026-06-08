"""段级远场/环境音门控（方案 1）：纯函数 + 会话级噪声底估计。

实时链路在 VAD 切出候选语音段后、送 ASR 前调用，挡掉"清晰但很远/很轻"的段：
    ① 绝对能量门（dBFS）——全局响度过低直接丢弃；
    ② 自适应信噪比门（SNR）——相对会话环境底噪不够突出则丢弃（远场核心特征）。
两道门为"或"关系，任一触发即丢弃。输入音频须为 float32 [-1, 1)（满量程参考）。
"""
import numpy as np

# 静音兜底：避免 log10(0)；同时作为"纯静音帧不污染噪声底估计"的判据
DBFS_FLOOR = -120.0


def rms_dbfs(seg: np.ndarray) -> float:
    """段 RMS 转 dBFS（满量程参考）。空段 / 全零段返回 DBFS_FLOOR。"""
    if seg is None or seg.size == 0:
        return DBFS_FLOOR
    rms = float(np.sqrt(np.mean(np.square(seg.astype(np.float32)))))
    if rms <= 1e-9:
        return DBFS_FLOOR
    return 20.0 * float(np.log10(rms))


class NoiseFloorTracker:
    """会话级噪声底 EMA（dBFS 域），仅在非语音期以进帧更新，慢跟随环境本底。"""

    def __init__(self, alpha: float = 0.05):
        self._alpha = alpha
        self._floor_dbfs = None       # None = 未初始化（无静音样本前 SNR 门跳过）

    def update(self, frame: np.ndarray) -> None:
        d = rms_dbfs(frame)
        if d <= DBFS_FLOOR:
            return                    # 纯静音帧不参与底噪估计
        if self._floor_dbfs is None:
            self._floor_dbfs = d
        else:
            self._floor_dbfs = (1.0 - self._alpha) * self._floor_dbfs + self._alpha * d

    @property
    def floor_dbfs(self):
        return self._floor_dbfs


def should_gate(
    seg_dbfs: float,
    noise_floor_dbfs,
    *,
    energy_floor_dbfs: float,
    snr_min_db: float,
) -> tuple[bool, str]:
    """门控判定：返回 (是否丢弃, 命中门名称)。

    能量门：seg_dbfs < energy_floor_dbfs → 丢弃。
    SNR 门：snr_min_db > 0 且噪声底已初始化时，(seg_dbfs - noise_floor) < snr_min_db → 丢弃。
    """
    if seg_dbfs < energy_floor_dbfs:
        return True, "energy"
    if snr_min_db > 0 and noise_floor_dbfs is not None:
        if (seg_dbfs - noise_floor_dbfs) < snr_min_db:
            return True, "snr"
    return False, ""

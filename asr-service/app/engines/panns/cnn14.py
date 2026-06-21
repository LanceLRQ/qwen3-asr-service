"""PANNs CNN14 网络（vendored 自 qiuqiangkong/audioset_tagging_cnn，MIT）。

与官方实现的差异仅为「去训练期算子」：移除 SpecAugmentation（推理不用、无参数）与
dropout（eval 下恒等），其余结构、模块命名、前端参数与官方一致，故官方权重 state_dict
可逐键加载。前端 STFT + LogMel 直接复用 torchlibrosa（官方同款，免参数对齐）。

16k / 32k 两个变体共用本结构，仅 __init__ 的前端参数（window/hop/fmax/sample_rate）不同，
由 panns_tagger_engine 按变体传入。
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchlibrosa.stft import Spectrogram, LogmelFilterBank


class ConvBlock(nn.Module):
    """两层 3x3 卷积 + BN + ReLU，末尾按 pool_type 池化（对齐官方 ConvBlock）。"""

    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=(3, 3),
                               stride=(1, 1), padding=(1, 1), bias=False)
        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=(3, 3),
                               stride=(1, 1), padding=(1, 1), bias=False)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)

    def forward(self, x, pool_size=(2, 2), pool_type="avg"):
        x = F.relu_(self.bn1(self.conv1(x)))
        x = F.relu_(self.bn2(self.conv2(x)))
        if pool_type == "max":
            x = F.max_pool2d(x, kernel_size=pool_size)
        elif pool_type == "avg":
            x = F.avg_pool2d(x, kernel_size=pool_size)
        elif pool_type == "avg+max":
            x = F.avg_pool2d(x, kernel_size=pool_size) + F.max_pool2d(x, kernel_size=pool_size)
        return x


class Cnn14(nn.Module):
    """AudioSet 通用音频打标网络（默认 527 类 sigmoid 输出）。

    forward 输入 [B, samples]（单声道、采样率须与 sample_rate 一致），
    返回 {"clipwise_output": [B, classes], "embedding": [B, 2048]}。
    """

    def __init__(self, sample_rate: int, window_size: int, hop_size: int,
                 mel_bins: int, fmin: int, fmax: int, classes_num: int = 527):
        super().__init__()
        # 前端：STFT → LogMel（torchlibrosa，官方同款参数；权重 freeze）
        self.spectrogram_extractor = Spectrogram(
            n_fft=window_size, hop_length=hop_size, win_length=window_size,
            window="hann", center=True, pad_mode="reflect", freeze_parameters=True)
        self.logmel_extractor = LogmelFilterBank(
            sr=sample_rate, n_fft=window_size, n_mels=mel_bins, fmin=fmin, fmax=fmax,
            ref=1.0, amin=1e-10, top_db=None, freeze_parameters=True)

        self.bn0 = nn.BatchNorm2d(mel_bins)
        self.conv_block1 = ConvBlock(1, 64)
        self.conv_block2 = ConvBlock(64, 128)
        self.conv_block3 = ConvBlock(128, 256)
        self.conv_block4 = ConvBlock(256, 512)
        self.conv_block5 = ConvBlock(512, 1024)
        self.conv_block6 = ConvBlock(1024, 2048)
        self.fc1 = nn.Linear(2048, 2048, bias=True)
        self.fc_audioset = nn.Linear(2048, classes_num, bias=True)

    def forward(self, x: torch.Tensor) -> dict:
        x = self.spectrogram_extractor(x)   # [B, 1, frames, freq]
        x = self.logmel_extractor(x)        # [B, 1, frames, mel]

        x = x.transpose(1, 3)
        x = self.bn0(x)
        x = x.transpose(1, 3)

        x = self.conv_block1(x, pool_size=(2, 2), pool_type="avg")
        x = self.conv_block2(x, pool_size=(2, 2), pool_type="avg")
        x = self.conv_block3(x, pool_size=(2, 2), pool_type="avg")
        x = self.conv_block4(x, pool_size=(2, 2), pool_type="avg")
        x = self.conv_block5(x, pool_size=(2, 2), pool_type="avg")
        x = self.conv_block6(x, pool_size=(1, 1), pool_type="avg")

        x = torch.mean(x, dim=3)            # 频率维聚合
        x1, _ = torch.max(x, dim=2)
        x2 = torch.mean(x, dim=2)
        x = x1 + x2                         # 时间维 max+mean
        x = F.relu_(self.fc1(x))
        embedding = x
        clipwise_output = torch.sigmoid(self.fc_audioset(x))
        return {"clipwise_output": clipwise_output, "embedding": embedding}

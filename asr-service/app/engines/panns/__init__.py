# PANNs CNN14 模型定义（vendored 自 qiuqiangkong/audioset_tagging_cnn，MIT License）。
# 原始来源: https://github.com/qiuqiangkong/audioset_tagging_cnn (pytorch/models.py: Cnn14)
# 论文: Q. Kong et al., "PANNs: Large-Scale Pretrained Audio Neural Networks for Audio
#       Pattern Recognition", IEEE/ACM TASLP, 2020.
# 仅保留推理所需结构（去训练期 SpecAugmentation/dropout）；前端 logmel 用 torchlibrosa，
# 与官方预训练权重逐键对齐。许可详见仓库根 THIRD_PARTY_NOTICES.md。
from .cnn14 import Cnn14

__all__ = ["Cnn14"]

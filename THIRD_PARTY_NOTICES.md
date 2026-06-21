# Third-Party Notices / 第三方组件声明

本项目（qwen3-asr-service，MIT License）使用了以下第三方组件。其许可均为宽松型
（MIT / Apache-2.0 / CC-BY），与本项目 MIT 许可兼容，不影响本项目的开源许可。
vendored（随源码内置）部分保留各自版权与许可声明；运行时下载的模型权重不随仓库分发。

This project (qwen3-asr-service, MIT) uses the third-party components below. All are
permissively licensed (MIT / Apache-2.0 / CC-BY), compatible with this project's MIT
license. Vendored sources retain their original copyright/license; model weights are
downloaded at runtime and not redistributed in this repository.

---

## Vendored source / 随源码内置

### CAM++ speaker model (3D-Speaker)
- Path: `asr-service/app/engines/campplus/`
- Source: https://github.com/modelscope/3D-Speaker
- License: Apache License 2.0
- Usage: 说话人 embedding 网络结构（DTDNN），纯 torch 推理。

### PANNs CNN14 audio tagging model
- Path: `asr-service/app/engines/panns/`
- Source: https://github.com/qiuqiangkong/audioset_tagging_cnn
- License: MIT License
- Reference: Q. Kong et al., "PANNs: Large-Scale Pretrained Audio Neural Networks for
  Audio Pattern Recognition", IEEE/ACM TASLP, 2020.
- Usage: 通用音频事件标注网络结构（去训练期算子），纯 torch 推理。

### AudioSet class labels / 类目表
- Path: `asr-service/app/data/audioset_labels.csv`（527 类）、`asr-service/app/data/yamnet_labels.csv`（521 类）
- Source: Google AudioSet ontology (https://research.google.com/audioset/)
- License: Creative Commons Attribution 4.0 International (CC-BY-4.0)
- Citation: J. F. Gemmeke et al., "Audio Set: An ontology and human-labeled dataset for
  audio events", ICASSP, 2017.

---

## Python dependencies / 运行依赖（pip，不随仓库分发）

| Component | License | Used for |
|-----------|---------|----------|
| torch / torchaudio | BSD-3-Clause | 模型推理 |
| torchlibrosa | MIT | PANNs logmel 前端（1:1 复现官方精度） |
| librosa | ISC | 音频重采样 |
| funasr | MIT | VAD / 标点 / 对齐模型加载 |
| tflite-runtime / ai-edge-litert | Apache-2.0 | YAMNet 推理（可选依赖，requirements-yamnet.txt） |

---

## Model weights / 模型权重（运行时下载，不随仓库分发）

| Model | Source | License |
|-------|--------|---------|
| Qwen3-ASR / Qwen3-ForcedAligner | HuggingFace / ModelScope `Qwen/*` | Apache-2.0 |
| FSMN-VAD / CT-Transformer 标点 / CAM++ 权重 | ModelScope `iic/*` | Apache-2.0 |
| PANNs CNN14 16k 权重 | Zenodo record 3987831 (`Cnn14_16k_mAP=0.438.pth`) | Apache-2.0 |
| PANNs CNN14 32k 权重 | HuggingFace `nicofarr/panns_Cnn14` | Apache-2.0 |
| YAMNet TFLite | HuggingFace `thelou1s/yamnet`（Google 官方 TFLite） | Apache-2.0 |

> 各模型权重的许可以其发布方页面为准；本项目仅在运行时按需下载，不再分发。

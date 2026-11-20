# S1–S4 开发复核（2026-11-20）

复核起点为 `b445585`。从网关的 HTTP 和 WebSocket 入口检查能力声明、转写结果、任务取消、协议结束及资源释放。下面区分本轮复跑和此前已归档的验证。

| 阶段 | 本轮核对与复跑 | 结论 |
| --- | --- | --- |
| S1 接入与基线 | 重新生成五种音频，核对五份权重摘要，运行带鉴权的同步、异步和实时示例。RTX 3090 上重跑 standard，检查短句、对话、静音、噪声和长音频。 | 三个示例通过。五份离线业务结果与上轮逐字段一致。原生实时曾在 stop 后返回传输层关闭码 1006，本次修复后实测收到 session.closed 和 1000。 |
| S2 独立离线服务 | 重启独立 qwen-asr-serve，实际请求模型发现、文件转写及实时握手。运行远端失败、取消和文件清理测试。 | 短句 HTTP 转写成功，旧上游的 /v1/realtime 仍返回 403。S2 存档的长音频为 180 个参考词、5 次编辑错误。 |
| S3 离线核心 | 加入取消发生于转写、时间戳重试、标点、独立对齐期间的回归，并核对请求关闭声纹识别时的参数提示。另在远端 Qwen 同时开启词对齐与说话人处理，重跑五种音频。 | 修复取消后继续启动处理及声纹阈值提示缺失。五份远端业务结果与上轮逐字段一致。原有六种装配组合见 S3 归档。 |
| S4 远端实时与第二模型 | 修复 OpenAI delta 的修订基准、重复 commit 的冲刷积压，以及通用整句 HTTP 上游的自动语种声明。CPU 上重跑 PocketSphinx 英文对话。通过独立 Qwen streaming demo 跑三协议增量矩阵。 | 第二模型的整个响应记录与存档一致，三段、18 个对齐词。预热后的三协议矩阵 12/12 通过，另测慢上游积压和冷启动。 |

S1 至 S3 的离线对照包含全文、段边界和启用的辅助字段；对照排除了 task_id、耗时等运行元数据。原有 S4 整句实时矩阵包含 15 个真机场景；本轮新增的是独立增量协议的实测。验证音频为合成英文，双音色短对话的说话人仍可能全部标为 A。跨主机网络时延和多实例吞吐尚无测量数据。

[standard 基线](s1-standard-gpu/README.md) · [离线 HTTP 基线](s2-remote/README.md) · [S3 配置与偏差](s3-offline/README.md) · [整句实时矩阵](s4-realtime/README.md) · [第二模型](s4-second-model/README.md) · [增量协议与真机证据](s4-upstream/README.md)

## 验证与变更位置

完整测试在 Python 3.12.3 的项目 venv 中运行，`python -m pytest -o addopts='' -q` 共 1,188 项通过。S1 带鉴权的三个协议示例全部通过，DashScope 示例另在启用增量的真实网关上取得英文整句。Python 3.10 的超时路径由兼容测试覆盖，本轮实际运行环境为 Python 3.12。

本轮修改文件及相对于复核起点的首个修改行如下。

- `asr-service/app/api/compat/openai_ws_routes.py:53`
- `asr-service/app/api/compat/ws_bridge.py:31`
- `asr-service/app/api/schemas.py:98`
- `asr-service/app/api/ws_routes.py:64`
- `asr-service/app/main.py:484`
- `asr-service/app/pipeline/asr_pipeline.py:92`
- `asr-service/app/runtime/remote_stream_session.py:1`
- `asr-service/app/runtime/stream_session.py:697`
- `asr-service/app/runtime/vllm_offline.py:144`
- `asr-service/app/utils/arg_schema.py:48`
- `asr-service/app/web/assets/stream.js:38`
- `asr-service/config.example.yaml:8`
- `asr-service/scripts/e2e/README.md:75`
- `asr-service/scripts/e2e/compat_e2e.py:329`
- `asr-service/tests/unit/api/test_compat_fetch.py:36`
- `asr-service/tests/unit/api/test_compat_ws.py:332`
- `asr-service/tests/unit/api/test_remote_asr.py:75`
- `asr-service/tests/unit/api/test_ws_routes.py:226`
- `asr-service/tests/unit/pipeline/test_direct_offline.py:39`
- `asr-service/tests/unit/pipeline/test_remote_offline.py:144`
- `asr-service/tests/unit/runtime/test_remote_incremental_session.py:1`
- `asr-service/tests/unit/runtime/test_stream_session.py:824`
- `asr-service/tests/unit/runtime/test_vllm_offline.py:405`
- `asr-service/tests/unit/utils/test_arg_schema.py:15`
- `docs/api/compat.md:56`
- `docs/api/compat_EN.md:56`
- `docs/api/v2/basics.md:154`
- `docs/api/v2/basics_EN.md:151`
- `docs/architecture.md:9`
- `docs/architecture_EN.md:9`
- `docs/baselines/review-s1-s4.md:1`
- `docs/baselines/s4-realtime/README.md:7`
- `docs/baselines/s4-upstream/README.md:1`
- `docs/baselines/s4-upstream/cold-start.json:1`
- `docs/baselines/s4-upstream/demo-model.json:1`
- `docs/baselines/s4-upstream/demo-stream.json:1`
- `docs/baselines/s4-upstream/demo_stream.py:1`
- `docs/baselines/s4-upstream/gateway.json:1`
- `docs/baselines/s4-upstream/pressure.json:1`
- `docs/baselines/s4-upstream/streaming.py:1`
- `docs/configuration.md:33`
- `docs/configuration_EN.md:33`
- `docs/deployment.md:11`
- `docs/deployment_EN.md:11`

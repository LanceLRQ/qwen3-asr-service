from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engines.asr_result import ASRResult, BackendCapabilities


@pytest.mark.parametrize("mode", ["standard", "vllm"])
def test_direct_and_queued_results(mode, tmp_path, make_wav, tm_factory, monkeypatch):
    from app.pipeline.asr_pipeline import ASRPipeline
    from app.runtime.task_manager import offline_processor
    from app.runtime.vllm_offline import run_vllm_offline

    monkeypatch.chdir(tmp_path)
    source = Path(make_wav())
    engine = SimpleNamespace(capabilities=BackendCapabilities(),
                             transcribe=Mock(return_value=ASRResult("你好。")))
    if mode == "standard":
        run = ASRPipeline(engine, SimpleNamespace(detect=lambda path: [(0, 2000)]),
                          work_dir=tmp_path).run
    else:
        run = partial(run_vllm_offline, engine, work_dir=tmp_path)
    options = {"with_words": True}
    direct = run(str(source), language="Chinese", options=options)
    assert direct["full_text"] == "你好。"
    assert list(tmp_path.iterdir()) == [source]

    manager = tm_factory()
    manager.set_processor(offline_processor(manager, run))
    manager.start()
    task_id = manager.submit(str(source), language="Chinese", options=options)
    task = manager.wait_done(task_id, timeout=5)
    assert task["status"] == "completed"
    assert task["result"] == direct
    assert source.exists()


@pytest.mark.parametrize("mode", ["standard", "vllm"])
@pytest.mark.parametrize("option", ["speaker_id_threshold", "speaker_id_margin"])
def test_identification_option_warns_when_request_skips_identification(mode, option, make_wav):
    from app.pipeline.asr_pipeline import ASRPipeline
    from app.runtime.vllm_offline import run_vllm_offline

    engine = SimpleNamespace(capabilities=BackendCapabilities(),
                             transcribe=Mock(return_value=ASRResult("")))
    service = Mock()
    components = {"speaker_engine": Mock(), "speaker_service": service}
    if mode == "standard":
        run = ASRPipeline(engine, SimpleNamespace(detect=lambda _: []), **components).run
    else:
        run = partial(run_vllm_offline, engine, **components)
    result = run(make_wav(), identify_speakers=False, options={option: 0.2})
    assert result.get("warnings") == ["speaker_id_threshold/margin"]
    service.map_and_enroll_clusters.assert_not_called()

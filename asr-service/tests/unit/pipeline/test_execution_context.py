import subprocess
import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engines.asr_result import BackendCapabilities


@pytest.mark.parametrize("mode", ["standard", "vllm"])
@pytest.mark.parametrize("outcome", ["success", "cancel", "failure"])
def test_input_and_workspace_ownership(mode, outcome, monkeypatch, tmp_path):
    from app.pipeline import asr_pipeline
    from app.runtime import vllm_offline

    source = tmp_path / "audio.wav"
    source.write_bytes(b"original")
    engine = SimpleNamespace(capabilities=BackendCapabilities())
    module = asr_pipeline if mode == "standard" else vllm_offline

    def convert(src, dst):
        assert Path(src) == source
        Path(dst).write_bytes(b"converted")
        if outcome == "failure":
            raise ValueError("conversion failed")

    monkeypatch.setattr(module, "convert_to_wav", convert)
    monkeypatch.setattr(module, "get_audio_duration", lambda path: 2)
    monkeypatch.setattr(vllm_offline, "_transcribe_progressive", lambda *args: [])
    if mode == "standard":
        vad = Mock(detect=Mock(return_value=[(0, 1000)] if outcome == "cancel" else []))
        pipeline = asr_pipeline.ASRPipeline(engine, vad, work_dir=tmp_path)
        monkeypatch.setattr(pipeline, "_split_segments_to_chunks", lambda *args: [])
        run = pipeline.run
    else:
        run = partial(vllm_offline.run_vllm_offline, engine, work_dir=tmp_path)

    if outcome == "failure":
        with pytest.raises(ValueError, match="conversion failed"):
            run(str(source))
    else:
        run(str(source), cancelled=lambda: outcome == "cancel")
    assert list(tmp_path.iterdir()) == [source]
    assert source.read_bytes() == b"original"


def test_independent_imports():
    subprocess.run([sys.executable, "-c", """
import sys
for name in ("fastapi", "app.api", "app.runtime.task_manager", "app.runtime.task_store",
             "qwen_asr", "funasr", "vllm", "torch"):
    sys.modules[name] = None
from app.pipeline.asr_pipeline import ASRPipeline
from app.runtime.vllm_offline import run_vllm_offline
"""], check=True)

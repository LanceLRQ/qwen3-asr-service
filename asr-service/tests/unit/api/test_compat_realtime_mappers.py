"""app/api/compat/mappers.py 实时部分测试（final → OpenAI completed / DashScope result）。

单位红线：final 顶层 start/end = 毫秒（直取）；final.words[].start/end = 秒（×1000）。
"""
from app.api.compat.mappers import (
    final_to_dashscope_result,
    final_to_openai_completed,
)

FINAL = {
    "type": "final", "seg_id": 0, "text": "你好世界",
    "start": 1000, "end": 4200,   # 毫秒
    "words": [{"text": "你好", "start": 1.0, "end": 2.0},   # 秒
              {"text": "世界", "start": 2.0, "end": 4.2}],
}


# ─── OpenAI completed ───

def test_openai_completed_structure():
    ev = final_to_openai_completed(FINAL, "item_0")
    assert ev == {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "item_0",
        "content_index": 0,
        "transcript": "你好世界",
    }


def test_openai_completed_no_words_field():
    # OpenAI completed 不带词级
    ev = final_to_openai_completed(FINAL, "item_5")
    assert "words" not in ev and "transcript" in ev


def test_openai_completed_empty_text():
    ev = final_to_openai_completed({"text": "", "start": 0, "end": 0}, "item_1")
    assert ev["transcript"] == ""


# ─── DashScope result-generated ───

def test_dashscope_result_envelope():
    ev = final_to_dashscope_result(FINAL, "task-abc")
    assert ev["header"]["task_id"] == "task-abc"
    assert ev["header"]["event"] == "result-generated"
    sent = ev["payload"]["output"]["sentence"]
    assert sent["sentence_end"] is True
    assert sent["text"] == "你好世界"


def test_dashscope_result_toplevel_ms_direct():
    # 顶层 start/end 已是毫秒，直取（不再 ×1000）
    ev = final_to_dashscope_result(FINAL, "t")
    sent = ev["payload"]["output"]["sentence"]
    assert sent["begin_time"] == 1000 and sent["end_time"] == 4200


def test_dashscope_result_words_sec_to_ms():
    # words 是秒 → ×1000
    ev = final_to_dashscope_result(FINAL, "t")
    words = ev["payload"]["output"]["sentence"]["words"]
    assert words == [
        {"begin_time": 1000, "end_time": 2000, "text": "你好", "punctuation": ""},
        {"begin_time": 2000, "end_time": 4200, "text": "世界", "punctuation": ""},
    ]


def test_dashscope_result_no_words():
    ev = final_to_dashscope_result({"text": "x", "start": 0, "end": 500}, "t")
    assert "words" not in ev["payload"]["output"]["sentence"]

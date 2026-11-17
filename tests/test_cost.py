"""비용 관리: 토큰·비용 기록, 가격 찾기, 오래된 대화 요약."""

import json
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from core import agent, usage
from test_agent import FakeClient, chunk, reply


def use(inp, out, cached=0):
    return NS(prompt_tokens=inp, completion_tokens=out, prompt_tokens_details=NS(cached_tokens=cached))


# ---------------------------------------------------------------- 가격·기록
def test_cost_counts_cached_input_cheaper():
    # mini: 입력 0.75, 캐시 0.075, 출력 4.5 ($/1M)
    assert usage.cost_of("gpt-5.4-mini", 1_000_000, 0, 0) == pytest.approx(0.75)
    assert usage.cost_of("gpt-5.4-mini", 1_000_000, 1_000_000, 0) == pytest.approx(0.075)
    assert usage.cost_of("gpt-5.4-mini", 0, 0, 1_000_000) == pytest.approx(4.5)


def test_price_lookup_dated_name_and_env_override(monkeypatch):
    assert usage.price_of("gpt-5.4-mini-2026-03-17") == usage.PRICES["gpt-5.4-mini"]  # gpt-5.4 로 잘못 안 잡힘
    assert usage.price_of("모르는-모델") is None
    monkeypatch.setenv("PRICE_GPT_5_4_MINI", "1,0.1,5")
    assert usage.price_of("gpt-5.4-mini") == (1.0, 0.1, 5.0)


def test_record_adds_to_session_and_file():
    usage.record("gpt-5.4-mini", use(10_000, 1_000, cached=4_000))
    usage.record("모르는-모델", {"prompt_tokens": 50, "completion_tokens": 5})  # dict 도 받고, 가격 모르면 0원
    usage.record("gpt-5.4", None)  # usage 없는 응답은 무시

    assert usage.SESSION["calls"] == 2 and usage.SESSION["input"] == 10_050
    expected = (6_000 * 0.75 + 4_000 * 0.075 + 1_000 * 4.5) / 1_000_000
    assert usage.SESSION["cost"] == pytest.approx(expected)
    today = usage.totals(datetime.now().strftime("%Y-%m-%d"))
    assert today["calls"] == 2 and today["cost"] == pytest.approx(expected)
    assert set(today["by_model"]) == {"gpt-5.4-mini", "모르는-모델"}
    assert usage.fmt_cost(0.0042) == "$0.0042" and usage.fmt_cost(1.234) == "$1.23"


def test_run_turn_records_usage_for_stream_and_plain():
    client = FakeClient([reply("안녕", usage=use(100, 10))])
    agent.run_turn(client, agent.new_conversation() + [{"role": "user", "content": "hi"}])
    assert usage.SESSION["calls"] == 1

    stream = [chunk("안"), chunk("녕"), NS(choices=[], usage=use(200, 20))]  # 사용량은 마지막 빈 조각에
    client = FakeClient([lambda s: iter(stream)])
    agent.run_turn(client, agent.new_conversation() + [{"role": "user", "content": "hi"}], on_text=lambda k, t: None)
    assert usage.SESSION["calls"] == 2 and usage.SESSION["input"] == 300


# ---------------------------------------------------------------- 오래된 대화 요약
def long_history(turns=8, size=4000):
    """질문 → 도구 호출 → 도구 결과 → 답 이 turns 번 반복되는 긴 기록."""
    msgs = agent.new_conversation()
    for i in range(turns):
        msgs += [
            {"role": "user", "content": f"질문 {i} " + "가" * size},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": "search_study_notes", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "결과 " * 500},
            {"role": "assistant", "content": f"답 {i}"},
        ]
    return msgs


def test_compact_keeps_recent_turns_and_pairs(monkeypatch):
    monkeypatch.setattr(agent, "SUMMARY_TRIGGER", 5_000)
    msgs = long_history()
    recent = msgs[-4 * agent.KEEP_RECENT_TURNS:]  # 최근 4개 질문(각 4개 메시지)은 원문 그대로여야 함
    client = FakeClient([reply("- 사용자가 질문 0~3을 함", usage=use(1000, 50))])
    notes = []

    assert agent.compact_history(client, msgs, on_tool=lambda n, a: notes.append(n)) is True
    assert msgs[0]["content"] == agent.SYSTEM_PROMPT
    assert msgs[1]["role"] == "system" and msgs[1]["content"].startswith(agent.SUMMARY_HEAD)
    assert msgs[2:] == recent
    assert msgs[2]["role"] == "user"  # 자른 곳이 질문 앞이라 도구 호출·결과가 안 갈라짐
    assert client.models == [agent.MODEL]  # 요약은 싼 모델로
    assert "질문 0" in client.sent[1]["content"] and "질문 7" not in client.sent[1]["content"]
    assert notes and notes[0].startswith("🗜")
    assert usage.SESSION["calls"] == 1  # 요약 비용도 기록


def test_compact_skips_short_heavy_and_failure(monkeypatch):
    monkeypatch.setattr(agent, "SUMMARY_TRIGGER", 5_000)
    short = agent.new_conversation() + [{"role": "user", "content": "hi"}]
    assert agent.compact_history(FakeClient([]), short) is False  # 짧으면 모델 호출도 안 함

    heavy = long_history()
    heavy[-1] = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "h", "type": "function", "function": {"name": "get_note_template", "arguments": json.dumps({"name": "최종갱신"}, ensure_ascii=False)}}]}
    heavy.append({"role": "tool", "tool_call_id": "h", "content": "템플릿"})
    before = list(heavy)
    assert agent.compact_history(FakeClient([]), heavy) is False  # 최종 갱신 중엔 안 줄임
    assert heavy == before

    def boom(stream):
        raise RuntimeError("API 오류")

    msgs = long_history()
    before = list(msgs)
    assert agent.compact_history(FakeClient([boom]), msgs) is False  # 요약 실패해도 기록은 그대로
    assert msgs == before


def test_run_turn_compacts_before_asking(monkeypatch):
    monkeypatch.setattr(agent, "SUMMARY_TRIGGER", 5_000)
    msgs = long_history() + [{"role": "user", "content": "새 질문"}]
    before = agent.estimate_tokens(msgs)
    client = FakeClient([reply("- 요약"), reply("답")])
    assert agent.run_turn(client, msgs) == "답"
    assert client.sent[1]["content"].startswith(agent.SUMMARY_HEAD)  # 실제 질문은 줄인 기록으로 보냄
    assert agent.estimate_tokens(client.sent) < before / 2


def test_no_repeated_compaction_when_old_part_is_small(monkeypatch):
    """최근 4개 질문만으로 기준을 넘어도, 이미 요약한 뒤라 옛 부분이 작으면 또 요약하지 않음."""
    monkeypatch.setattr(agent, "SUMMARY_TRIGGER", 5_000)
    msgs = long_history()
    assert agent.compact_history(FakeClient([reply("- 요약")]), msgs) is True
    msgs += long_history(turns=1)[1:]  # 질문 하나 더
    assert agent.compact_history(FakeClient([]), msgs) is False  # 모델 호출 없이 넘어감

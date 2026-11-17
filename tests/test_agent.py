"""에이전트 루프: 모델 고르기, 도구 호출 반복, 스트리밍 조각 조립. OpenAI 는 가짜 클라이언트."""

import json
from types import SimpleNamespace as NS

from core import agent


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


# ---------------------------------------------------------------- pick_model
def test_pick_model_switches_to_heavy_and_back():
    msgs = agent.new_conversation() + [{"role": "user", "content": "오늘 자료로 최종 갱신해줘"}]
    assert agent.pick_model(msgs) == agent.MODEL

    msgs.append({"role": "assistant", "content": None, "tool_calls": [call("get_note_template", {"name": "최종갱신"})]})
    assert agent.pick_model(msgs) == agent.HEAVY_MODEL

    msgs.append({"role": "assistant", "content": "이렇게 바꿀게. 반영할까?"})  # 계획만 보여 준 턴 → 계속 heavy
    msgs.append({"role": "user", "content": "응 반영해"})
    assert agent.pick_model(msgs) == agent.HEAVY_MODEL

    msgs.append({"role": "assistant", "content": None, "tool_calls": [call("update_page_section", {"title": "x"})]})
    msgs.append({"role": "assistant", "content": "반영 끝"})  # 반영 후 답한 턴 → 다시 싼 모델
    assert agent.pick_model(msgs) == agent.MODEL


def test_other_template_stays_light():
    msgs = [{"role": "assistant", "content": None, "tool_calls": [call("get_note_template", {"name": "강의노트"})]}]
    assert agent.pick_model(msgs) == agent.MODEL


# ---------------------------------------------------------------- run_turn (한 번에 받기)
class FakeClient:
    """미리 정해 둔 응답을 차례대로 돌려줌. stream=True 면 조각(chunk) 목록을 돌려줌."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.models = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, model, messages, tools=None, stream=False, **kw):
        self.models.append(model)
        self.sent = messages
        r = self.replies.pop(0)
        return r(stream) if callable(r) else r


def reply(content=None, tool_calls=(), usage=None):
    calls = [NS(id=c["id"], function=NS(name=c["function"]["name"], arguments=c["function"]["arguments"]))
             for c in tool_calls]
    return NS(choices=[NS(message=NS(content=content, refusal=None, tool_calls=calls or None))], usage=usage)


def test_run_turn_runs_tool_then_answers():
    client = FakeClient([reply(tool_calls=[call("calculate", {"expression": "1200*3/4"})]), reply("900이야")])
    msgs = agent.new_conversation() + [{"role": "user", "content": "1200*3/4?"}]
    seen = []
    answer = agent.run_turn(client, msgs, on_tool=lambda n, a: seen.append(n))

    assert answer == "900이야"
    assert seen == ["calculate"]
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["content"] == "900.0" and tool_msg["tool_call_id"] == "c1"


def test_unknown_tool_and_bad_args_do_not_crash():
    client = FakeClient([
        reply(tool_calls=[{"id": "a", "type": "function", "function": {"name": "nope", "arguments": "{}"}},
                          {"id": "b", "type": "function", "function": {"name": "calculate", "arguments": "{깨진"}}]),
        reply("미안"),
    ])
    msgs = agent.new_conversation() + [{"role": "user", "content": "?"}]
    assert agent.run_turn(client, msgs) == "미안"
    results = [m["content"] for m in msgs if m["role"] == "tool"]
    assert "알 수 없는 도구" in results[0] and "도구 실행 오류" in results[1]


def test_run_turn_stops_after_max_steps(monkeypatch):
    monkeypatch.setattr(agent, "MAX_STEPS", 3)
    client = FakeClient([reply(tool_calls=[call("get_current_time", {})]) for _ in range(3)])
    msgs = agent.new_conversation() + [{"role": "user", "content": "?"}]
    assert "너무 많아" in agent.run_turn(client, msgs)


# ---------------------------------------------------------------- 스트리밍
def chunk(content=None, tc=None):
    return NS(choices=[NS(delta=NS(content=content, refusal=None, tool_calls=tc))])


def tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


def test_stream_assembles_split_tool_call_and_text():
    first = [chunk("확인해"), chunk("볼게"),
             chunk(tc=[tc(0, id="c1", name="calcu")]), chunk(tc=[tc(0, name="late", args='{"expr')]),
             chunk(tc=[tc(0, args='ession": "2**10"}')]), NS(choices=[])]  # 마지막 사용량 조각은 choices 가 비어 있음
    second = [chunk("1024"), chunk("야")]
    client = FakeClient([lambda s: iter(first), lambda s: iter(second)])
    msgs = agent.new_conversation() + [{"role": "user", "content": "2의 10제곱"}]
    events = []
    answer = agent.run_turn(client, msgs, on_text=lambda k, t: events.append((k, t)))

    assert answer == "1024야"
    assert ("end", "확인해볼게") in events and ("end", "1024야") in events
    assert [t for k, t in events if k == "delta"] == ["확인해", "볼게", "1024", "야"]
    call_msg = next(m for m in msgs if m.get("tool_calls"))
    assert call_msg["tool_calls"][0]["function"] == {"name": "calculate", "arguments": '{"expression": "2**10"}'}
    assert next(m for m in msgs if m["role"] == "tool")["content"] == "1024"

"""웹 화면의 파이썬 쪽(Api)을 창 없이 직접 불러서 확인. 모델·Notion 은 가짜."""

import time

import pytest
from webview.util import parse_file_type

import ui.web_app as W


@pytest.fixture
def api(monkeypatch):
    def fake_run_turn(client, messages, on_tool=None, on_text=None):
        on_tool("⚡ 갱신 작업이라 상위 모델로 전환", "gpt-5.4")
        on_tool("search_study_notes", '{"keywords": "x"}')
        for ch in ["안", "녕"]:
            on_text("delta", ch)
        on_text("end", "안녕")
        return "안녕"

    monkeypatch.setattr(W, "run_turn", fake_run_turn)
    monkeypatch.setattr(W, "summarize_pdfs", lambda c, atts, title, t: f"# {title}\n({len(atts)}개)")
    monkeypatch.setattr(W.notion_tools, "suggest_titles", lambda rule, n: [(f"{89 + i}일차", "2026-11-16") for i in range(n)])
    monkeypatch.setattr(W.notion_tools, "create_note", lambda *a: {"url": "https://notion.so/x"})
    a = W.Api()
    a._client = object()
    return a


def drain(api, until: str = "done", count: int = 1):
    """화면처럼 poll 을 반복해서, until 종류 이벤트가 count 개 올 때까지 모음 (최대 3초)."""
    deadline = time.time() + 3
    events = []
    while time.time() < deadline and sum(e["type"] == until for e in events) < count:
        time.sleep(0.02)
        events += api.poll()["events"]
    return events


def test_file_dialog_types_are_valid():
    for ft in W._dialog_file_types():
        parse_file_type(ft)  # 형식이 틀리면 ValueError


def test_only_intended_methods_are_exposed():
    exposed = {n for n in dir(W.Api) if not n.startswith("_")}
    assert exposed == {"init", "poll", "save_api_key", "save_notion_token", "open_url", "new_chat", "send",
                       "pick_files", "add_files", "attachments", "clear_attachments", "make_note_cards", "save_card"}


def test_send_streams_events_in_order(api):
    assert api.send("테스트")["ok"]
    types = [e["type"] for e in drain(api)]
    assert types == ["heavy", "tool", "s_delta", "s_delta", "s_end", "final", "done"]
    assert api.send("")["ok"] is False  # 빈 메시지는 안 보냄


def test_usage_label_and_conversation_reset(api):
    from core import usage
    usage.record("gpt-5.4-mini", {"prompt_tokens": 1_000_000, "completion_tokens": 0})
    assert api.poll()["usage"]["label"] == "이번 대화 $0.75 · 오늘 $0.75"
    api.new_chat()
    assert api.poll()["usage"]["label"] == "이번 대화 $0.0000 · 오늘 $0.75"  # 새 대화는 0부터, 오늘 합계는 유지


def test_compact_notice_becomes_notice_event(api):
    api._on_tool("🗜 오래된 대화를 요약해서 줄임", "약 30,000 → 8,000 토큰")
    assert api.poll()["events"] == [{"type": "notice", "text": "🗜 오래된 대화를 요약해서 줄임 (약 30,000 → 8,000 토큰)"}]


def test_merge_makes_one_day_card(api, tmp_path):
    """하루치로 합치기(기본값): 파일 3개 → 카드 1개, 제목 하나 (89일차), 정리할 때 3개를 같이 보냄."""
    for n in ("회의록.md", "기획서.md", "화면.txt"):
        (tmp_path / n).write_text(n, encoding="utf-8")
    api.add_files([str(p) for p in sorted(tmp_path.iterdir())])
    r = api.make_note_cards(api.init()["templates"][0], merge=True)
    assert [c["title"] for c in r["cards"]] == ["89일차"]
    draft = next(e for e in drain(api, "card_draft") if e["type"] == "card_draft")
    assert draft["text"].endswith("(3개)")  # 가짜 summarize_pdfs 가 받은 파일 수


def test_merge_is_checked_by_default():
    html = (W.WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="merge" checked' in html


def test_send_without_key_asks_for_key(api):
    api._client = None
    assert api.send("hi") == {"ok": False, "need_key": True}


def test_drop_folder_then_cards_then_save(api, tmp_path):
    (tmp_path / "a.txt").write_text("첫 파일", encoding="utf-8")
    (tmp_path / "b.md").write_text("# 둘째", encoding="utf-8")
    (tmp_path / "x.exe").write_bytes(b"MZ")
    api._on_drop({"dataTransfer": {"files": [{"name": tmp_path.name, "pywebviewFullPath": str(tmp_path)}]}})
    events = api.poll()["events"]
    assert [e["type"] for e in events] == ["error", "attachments"]  # exe 는 오류, 나머지 2개 첨부
    assert len(events[1]["items"]) == 2

    r = api.make_note_cards(api.init()["templates"][0], merge=False)
    assert [c["title"] for c in r["cards"]] == ["89일차", "90일차"]
    assert r["attachments"]["items"] == []
    drafts = [e for e in drain(api, "card_draft", 2) if e["type"] == "card_draft"]
    assert sorted(e["id"] for e in drafts) == [1, 2]

    assert api.save_card(1, "", "", "강의", "")["ok"] is False
    assert api.save_card(1, "89일차", "내용", "강의", "2026-11-16")["ok"]
    assert {"type": "card_saved", "id": 1, "url": "https://notion.so/x"} in drain(api, "card_saved")

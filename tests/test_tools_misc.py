"""작은 부품들: @tool 스키마, 생활 도구, 마크다운 → 블록, 첨부 파일, 저장소 이름 찾기, 노트 저장 중복 방지."""

import pytest

import tools.github as github
import tools.notion.tools as ntools
from core.toolkit import tool
from study.attachments import build_user_content, drop_old_pdf_bodies, load_file
from tools import ALL_TOOLS, TOOL_SCHEMAS, life
from tools.notion.blocks import _markdown_to_blocks


# ---------------------------------------------------------------- @tool
def test_tool_schema_from_signature_and_docstring():
    @tool
    def sample(title: str, limit: int = 5, done: bool = False) -> str:
        """설명 첫 줄.

        Args:
            title: 제목.
            limit: 개수.
        """

    fn = sample.schema["function"]
    assert fn["description"] == "설명 첫 줄."
    assert fn["parameters"]["required"] == ["title"]
    assert fn["parameters"]["properties"]["limit"] == {"type": "integer", "description": "개수."}
    assert fn["parameters"]["properties"]["done"]["type"] == "boolean"


def test_all_tools_have_descriptions_and_unique_names():
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert len(names) == len(set(names)) == len(ALL_TOOLS) == 21
    assert all(s["function"]["description"] for s in TOOL_SCHEMAS)


# ---------------------------------------------------------------- 생활 도구
def test_calculate_is_safe():
    assert life.calculate("(1200 * 3) / 7") == str(1200 * 3 / 7)
    assert "계산 실패" in life.calculate("__import__('os').system('dir')")


def test_todos_roundtrip():
    assert "#1" in life.add_todo("회고 쓰기", "2026-11-21")
    life.add_todo("복습")
    assert "완료" in life.complete_todo(1)
    assert life.list_todos() == "#2 [ ] 복습"
    assert "#1 [x]" in life.list_todos(include_done=True)


# ---------------------------------------------------------------- 마크다운 → Notion 블록
def test_markdown_to_blocks_kinds():
    md = "### 제목\n- [x] 끝남\n- [ ] 할 일\n| a | b |\n|---|---|\n| 1 |\n```python\nx = 1\n```\n---\n문단"
    kinds = [b["type"] for b in _markdown_to_blocks(md)]
    assert kinds == ["heading_3", "to_do", "to_do", "table", "code", "divider", "paragraph"]
    table = _markdown_to_blocks("| a | b |\n|---|---|\n| 1 |")[0]["table"]
    assert table["table_width"] == 2
    assert len(table["children"][1]["table_row"]["cells"]) == 2  # 모자란 칸은 빈칸으로 채움


# ---------------------------------------------------------------- 첨부
def test_load_text_html_and_unsupported(tmp_path):
    (tmp_path / "a.md").write_text("# 회의록\n결정: LangGraph", encoding="utf-8")
    (tmp_path / "b.html").write_text("<html><script>x()</script><p>본문 <b>굵게</b></p></html>", encoding="utf-8")
    (tmp_path / "c.hwp").write_bytes(b"x")
    assert "LangGraph" in load_file(tmp_path / "a.md").text
    html = load_file(tmp_path / "b.html").text
    assert "본문" in html and "x()" not in html  # 스크립트는 빼고 글만
    with pytest.raises(ValueError, match="PDF로"):
        load_file(tmp_path / "c.hwp")


def test_old_attachment_bodies_are_dropped(tmp_path):
    (tmp_path / "a.md").write_text("긴 본문 " * 100, encoding="utf-8")
    msgs = [{"role": "user", "content": build_user_content("정리해줘", [load_file(tmp_path / "a.md")])}]
    drop_old_pdf_bodies(msgs)
    assert "긴 본문" not in msgs[0]["content"]
    assert "[첨부 파일: a.md]" in msgs[0]["content"]  # 제목 줄은 남김


# ---------------------------------------------------------------- GitHub 저장소 이름
def test_resolve_repo_url_and_fork(monkeypatch):
    assert github.resolve_repo("https://github.com/owner/name.git", "me") == "owner/name"

    def fake_get(path):
        if path.startswith("/users/me/repos"):
            return [{"name": "Team-Repo", "full_name": "me/Team-Repo", "fork": True}]
        if path == "/repos/me/Team-Repo":
            return {"parent": {"full_name": "org/Team-Repo"}}
        raise AssertionError(path)

    monkeypatch.setattr(github, "_get", fake_get)
    assert github.resolve_repo("team-repo", "me") == "org/Team-Repo"  # 내 포크면 원본 팀 저장소로


# ---------------------------------------------------------------- 노트 저장
def test_save_note_twice_creates_one_page(notion, cache_with):
    cache_with({})
    first = ntools.save_study_note("89일차", "## 오늘\n- 회의", "실습", "2026-11-16")
    second = ntools.save_study_note("89일차", "## 오늘\n- 회의", "실습", "2026-11-16")
    assert "저장함" in first and "이미 있어서" in second
    assert [m for m, p in notion.calls if (m, p) == ("POST", "/pages")] == ["POST"]


def test_bad_date_is_rejected_before_request(notion, cache_with):
    cache_with({})
    assert "날짜 형식" in ntools.save_study_note("메모", "내용", "", "11월 16일")
    assert not notion.calls

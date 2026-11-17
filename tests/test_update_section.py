"""최종 프로젝트 페이지 섹션 갱신 + 되돌리기. 앱에서 가장 위험한 기능이라 제일 꼼꼼히.

확인하는 것:
  - 고치라는 섹션만 바뀌고 다른 섹션·구분선은 그대로인지
  - append 는 섹션 끝(구분선 앞)에 붙는지
  - undo 가 원래 모습으로 정확히 돌아가는지 (Notion 이 after 응답에 뒤쪽 블록을 섞어 줘도)
"""

import tools.notion.history as nhistory
import tools.notion.tools as ntools

TITLE = "최종 프로젝트 — 테스트"
PAGE = """## 1. 개요
에이전트 비교 서비스임
---
## 2. 기술 스택
- Python
- FastAPI
---
## 9. 진행 기록
- 11/14 기획 확정
"""


def setup_page(notion, cache_with):
    pid = notion.add_page(TITLE, PAGE)
    cache_with({pid: {"title": TITLE}})
    nhistory.begin_batch()
    return pid


def test_replace_changes_only_target_section(notion, cache_with):
    pid = setup_page(notion, cache_with)
    before = notion.texts(pid)

    msg = ntools.update_page_section(TITLE, "기술 스택", "- Python\n- LangGraph\n- Postgres")

    after = notion.texts(pid)
    assert "교체함" in msg
    assert after[:4] == before[:4]  # 개요 섹션과 구분선은 그대로
    assert after[4:7] == ["bulleted_list_item: Python", "bulleted_list_item: LangGraph",
                          "bulleted_list_item: Postgres"]
    assert after[7:] == before[6:]  # 구분선과 진행 기록도 그대로


def test_append_goes_before_divider(notion, cache_with):
    pid = setup_page(notion, cache_with)
    ntools.update_page_section(TITLE, "기술 스택", "- Redis", mode="append")
    after = notion.texts(pid)
    i = after.index("bulleted_list_item: Redis")
    assert after[i - 1] == "bulleted_list_item: FastAPI"
    assert after[i + 1] == "divider: "


def test_undo_restores_exact_page(notion, cache_with):
    pid = setup_page(notion, cache_with)
    original = notion.texts(pid)

    nhistory.begin_batch()  # 한 질문 안에서 두 섹션을 고친 상황
    ntools.update_page_section(TITLE, "기술 스택", "- 전부 바뀜")
    ntools.update_page_section(TITLE, "진행 기록", "- 11/16 첫 갱신", mode="append")
    assert notion.texts(pid) != original

    msg = nhistory.undo_last_update()
    assert "2건" in msg
    assert notion.texts(pid) == original
    assert nhistory._history() == []  # 되돌린 기록은 지워짐


def test_undo_only_last_batch(notion, cache_with):
    pid = setup_page(notion, cache_with)
    nhistory.begin_batch()
    ntools.update_page_section(TITLE, "개요", "첫 번째 수정")
    after_first = notion.texts(pid)
    nhistory.begin_batch()
    ntools.update_page_section(TITLE, "개요", "두 번째 수정")

    nhistory.undo_last_update()
    assert notion.texts(pid) == after_first  # 두 번째 질문 것만 되돌아감


def test_missing_section_is_created_at_end_and_undoable(notion, cache_with):
    pid = setup_page(notion, cache_with)
    original = notion.texts(pid)
    msg = ntools.update_page_section(TITLE, "트러블슈팅", "- 없음")
    assert "새로 만듦" in msg
    assert notion.texts(pid)[-2:] == ["heading_2: 트러블슈팅", "bulleted_list_item: 없음"]
    nhistory.undo_last_update()
    assert notion.texts(pid) == original


def test_wrong_title_does_not_touch_anything(notion, cache_with):
    setup_page(notion, cache_with)
    msg = ntools.update_page_section("최종 프로젝트", "개요", "x")  # 제목 일부만 → 다른 페이지일 수 있어서 거절
    assert "못 찾음" in msg
    assert not [c for c in notion.calls if c[0] in ("PATCH", "DELETE")]

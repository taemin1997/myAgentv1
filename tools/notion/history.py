"""백업 & 되돌리기: 앱이 Notion을 고칠 때마다 무엇을 지우고 넣었는지 기록하고, 질문 단위로 되돌림."""

from datetime import datetime

from core.paths import DATA_DIR
from core.storage import read_json, update_json
from core.toolkit import tool

from .blocks import _append, _block, _children
from .cache import start_background_sync
from .client import NotionError, _request

_CREATED: dict[str, str] = {}  # 이번 실행에서 만든 페이지 제목 → url (중복 생성 방지)

# 앱이 Notion을 고칠 때마다 '무엇을 지우고 무엇을 넣었는지'를 data/update_history.json 에 남김.
# 같은 질문(run_turn 한 번) 안에서 한 수정은 같은 batch 로 묶어서, "방금 갱신 되돌려줘" 한 번에 다 되돌림.
HISTORY_FILE = DATA_DIR / "update_history.json"
HISTORY_KEEP = 40  # 오래된 기록은 버림
CURRENT_BATCH = {"id": "manual"}  # agent.run_turn 이 질문마다 새 id로 바꿈


def begin_batch() -> None:
    CURRENT_BATCH["id"] = datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _history() -> list[dict]:
    return read_json(HISTORY_FILE, [])


def _record(entry: dict) -> None:
    # 채팅 도구와 웹 화면 정리 카드가 동시에 기록할 수 있어서 읽기~쓰기를 잠금 하나로 (core/storage.py)
    item = {"batch": CURRENT_BATCH["id"], "time": datetime.now().strftime("%m/%d %H:%M"), **entry}
    update_json(HISTORY_FILE, [], lambda hist: (hist + [item])[-HISTORY_KEEP:])


def _clean_rich(items: list) -> list:
    """GET 으로 받은 rich_text → 다시 만들 때 쓸 수 있는 형태 (plain_text·href 같은 읽기 전용 값 빼기).
    다시 만들 수 없는 멘션(링크 미리보기 등)은 글자로 바꿈."""
    out = []
    for r in items:
        if r.get("type") == "mention" and r["mention"].get("type") not in ("page", "database", "date", "user"):
            r = {"type": "text", "text": {"content": r.get("plain_text", "")}, "annotations": r.get("annotations", {})}
        out.append({k: v for k, v in r.items() if k not in ("plain_text", "href")})
    return out


_NOT_RESTORABLE = {"child_page", "child_database", "unsupported", "link_preview", "synced_block", "template"}


def _serialize(block: dict, depth: int = 0) -> dict | None:
    """Notion 블록(GET 결과) → 같은 블록을 다시 만드는 요청 형태. 하위 블록(토글 안, 표의 행)까지 담음.
    Notion은 한 번에 2단계 아래까지만 만들 수 있어서 depth 2에서 멈춤."""
    t = block["type"]
    if t in _NOT_RESTORABLE:
        return None
    data = dict(block[t])
    if t in ("image", "file", "pdf", "video", "audio") and data.get("type") == "file":
        # 업로드 파일 주소는 1시간 뒤 만료돼서 다시 못 만듦 → 자리 표시만 남김
        return _block("paragraph", f"(되돌리기로 복구 못 한 첨부 파일: {t})")
    if "rich_text" in data:
        data["rich_text"] = _clean_rich(data["rich_text"])
    if "caption" in data:
        data["caption"] = _clean_rich(data["caption"])
    if t == "table_row":
        data["cells"] = [_clean_rich(c) for c in data["cells"]]
    if block.get("has_children") and depth < 2:
        kids = [s for c in _children(block["id"]) if (s := _serialize(c, depth + 1))]
        if kids:
            data["children"] = kids
    return {"object": "block", "type": t, t: data}


@tool
def undo_last_update() -> str:
    """앱이 마지막으로 한 Notion 수정(가장 최근 질문에서 한 섹션 갱신·페이지 생성 전부)을 되돌립니다.
    사용자가 '방금 갱신 되돌려줘', '원래대로 해줘', '잘못 고쳤어'라고 하면 쓰세요.
    교체한 섹션은 원래 내용으로 복구하고, 이어 쓴 줄은 지우고, 새로 만든 페이지는 휴지통으로 보냄.
    한 번 더 부르면 그 전 수정을 되돌림.
    """
    hist = _history()
    if not hist:
        return "되돌릴 수정 기록이 없음."
    batch = hist[-1]["batch"]
    todo = [e for e in hist if e["batch"] == batch]
    done, problems = [], []
    try:
        for e in reversed(todo):  # 나중에 한 것부터 거꾸로
            if e["kind"] == "create":
                _request("PATCH", f"/pages/{e['page_id']}", {"in_trash": True})
                _CREATED.pop(e["page_title"], None)
                done.append(f"새 페이지 '{e['page_title']}' 휴지통으로 보냄 (Notion 휴지통에서 복구 가능)")
                continue
            missing = 0
            for bid in e["new_ids"]:
                try:
                    _request("DELETE", f"/blocks/{bid}")
                except NotionError:
                    missing += 1  # 사용자가 이미 지웠거나 옮긴 블록
            if e["old_blocks"]:
                try:
                    _append(e["page_id"], e["old_blocks"], after=e["anchor"])
                except NotionError as err:  # 섹션 제목이 지워졌으면 끝에라도 붙임
                    _append(e["page_id"], [_block("heading_2", e["section"])] + e["old_blocks"])
                    problems.append(f"'{e['section']}' 제목을 못 찾아 페이지 끝에 복구함 ({err})")
            what = {"replace": "원래 내용으로 복구", "append": "추가한 줄 지움", "new": "새로 만든 섹션 지움"}[e["mode"]]
            done.append(f"'{e['page_title']}' › {e['section']}: {what}"
                        + (f" (이미 없어진 블록 {missing}개는 건너뜀)" if missing else ""))
    except NotionError as err:
        problems.append(str(err))
    # 되돌린 기록은 지움 (다음 되돌리기는 그 전 수정)
    # 되돌리는 동안 새로 쌓인 기록이 있을 수 있어서, 처음 읽은 hist 가 아니라 지금 파일에서 이 batch 만 뺌
    update_json(HISTORY_FILE, [], lambda now: [e for e in now if e["batch"] != batch])
    start_background_sync()
    when = todo[0]["time"]
    return (f"{when}에 한 수정 {len(todo)}건을 되돌림:\n" + "\n".join(f"- {d}" for d in done)
            + ("\n문제:\n" + "\n".join(f"- {p}" for p in problems) if problems else ""))

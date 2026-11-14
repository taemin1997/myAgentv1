"""로컬 캐시 & 동기화.

[왜 로컬 캐시를 쓰나]
Notion 공개 API의 검색(/v1/search)은 페이지 '제목'만 찾음.
노트 제목이 '45일차'처럼 날짜라서, "군집화"로 검색하면 아무것도 안 나옴.
그래서 노트 본문을 전부 받아서 data/notion_cache.json 에 저장해 두고,
검색은 그 캐시에서 직접 함. 수정 시각(last_edited_time)이 바뀐 페이지만
다시 받아오기 때문에 두 번째 동기화부터는 빠름.
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from core.paths import DATA_DIR

from .blocks import _page_text, _plain
from .client import (DATE_PROP, REVIEW_PROP, TITLE_PROP, TOPIC_PROP, TYPE_PROP, NotionError,
                     _data_source_id, _request)

CACHE_FILE = DATA_DIR / "notion_cache.json"
AUTO_SYNC_AFTER = timedelta(minutes=30)  # 마지막 동기화 후 이만큼 지나면 검색 전에 자동 동기화


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {"synced_at": None, "pages": {}}


def _save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def kst_date(iso: str) -> str:
    """Notion 시간(UTC, 예: 2026-10-19T23:41:00.000Z) → 한국 날짜(2026-10-20).
    UTC 그대로 앞 10글자를 자르면 오전 9시 전에 쓴 노트가 하루 전 날짜로 나옴."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


def _props(page: dict) -> dict:
    p = page["properties"]
    title = _plain(p.get(TITLE_PROP, {}).get("title", [])) or "(제목 없음)"
    sel = p.get(TYPE_PROP, {}).get("select")
    topic = p.get(TOPIC_PROP, {}).get("select")
    date = (p.get(DATE_PROP, {}).get("date") or {}).get("start")
    return {
        "title": title,
        # 목록에 보여줄 분류: 과목이 있으면 과목(예: 딥러닝), 없으면 유형(강의/실습)
        "type": topic["name"] if topic else (sel["name"] if sel else ""),
        "reviewed": bool(p.get(REVIEW_PROP, {}).get("checkbox")),
        # '날짜'를 채운 노트는 그 날짜, 아니면 작성일시(한국 시간). 목록 정렬과 표시에 씀
        "created": (date or kst_date(page["created_time"]))[:10],
        "edited": page["last_edited_time"],
        "url": page.get("url", ""),
    }


# 동기화 진행 상황. GUI가 이 값을 읽어서 "노션 동기화 중 12/95" 를 표시함.
SYNC_STATE = {"running": False, "done": 0, "total": 0, "error": None}
_sync_lock = threading.Lock()  # 동기화가 동시에 두 번 돌지 않게
# 동시 요청 수. 실측해 보니 1개(606초)나 3개(593초)나 거의 같았음.
# Notion이 토큰 하나당 '초당 약 3회'로 제한해서, 동시에 보내도 결국 429(대기)로 속도가 맞춰짐.
# → 첫 동기화 속도는 노트 양(토글 수)에 달려 있고, 대신 백그라운드로 돌려서 기다리지 않게 한 것.
WORKERS = 2


def _list_pages() -> list[dict]:
    """DB의 페이지 목록(속성만)을 전부 받음. 본문은 안 받아서 빠름 (100개당 요청 1번)."""
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = _request("POST", f"/data_sources/{_data_source_id()}/query", body)
        pages += res["results"]
        if not res.get("has_more"):
            return pages
        cursor = res["next_cursor"]


def _sync() -> str:
    """새로 생겼거나 수정된 페이지만 본문을 다시 받음.

    - 페이지 본문은 토글마다 요청이 필요해서 오래 걸림 (첫 동기화 95개 기준 수 분)
    - 그래서 WORKERS개씩 동시에 받고, 몇 개 받을 때마다 캐시를 저장함
      → 중간에 프로그램을 꺼도 받은 데까지는 남아 있음
    """
    with _sync_lock:
        SYNC_STATE.update(running=True, done=0, total=0, error=None)
        try:
            cache = _load_cache()
            old = cache["pages"]
            listed = _list_pages()

            # 속성(제목/복습 여부 등)은 목록에서 바로 갱신하고, 본문은 바뀐 것만 다시 받을 목록에 넣음
            pages, todo = {}, []
            for page in listed:
                info = _props(page)
                prev = old.get(page["id"])
                if prev and prev["edited"] == info["edited"]:
                    info["text"] = prev["text"]
                else:
                    info["text"] = prev["text"] if prev else ""  # 다 받기 전까지는 예전 내용으로 검색
                    info["edited"] = prev["edited"] if prev else ""  # 실패하면 다음에 다시 받도록
                    todo.append((page["id"], page["last_edited_time"]))
                pages[page["id"]] = info
            cache["pages"] = pages  # DB에서 지워진 페이지는 여기서 빠짐
            _save_cache(cache)

            SYNC_STATE["total"] = len(todo)
            failed = 0
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {pool.submit(_page_text, pid): (pid, edited) for pid, edited in todo}
                for i, fut in enumerate(as_completed(futures), 1):
                    pid, edited = futures[fut]
                    try:
                        pages[pid]["text"], pages[pid]["edited"] = fut.result(), edited
                    except NotionError:
                        failed += 1  # 이 페이지만 건너뛰고 계속. 다음 동기화 때 다시 시도됨
                    SYNC_STATE["done"] = i
                    if i % 5 == 0:
                        _save_cache(cache)

            cache["synced_at"] = datetime.now(timezone.utc).isoformat()
            _save_cache(cache)
            msg = f"동기화 완료: 노트 {len(pages)}개 (새로 받은 것 {len(todo) - failed}개)"
            return msg + (f", 실패 {failed}개는 다음에 다시 시도함" if failed else "")
        except NotionError as e:
            SYNC_STATE["error"] = str(e)
            raise
        finally:
            SYNC_STATE["running"] = False


def start_background_sync() -> bool:
    """백그라운드 스레드로 동기화 시작. 이미 돌고 있으면 아무것도 안 함."""
    if SYNC_STATE["running"] or not os.getenv("NOTION_TOKEN"):
        return False
    SYNC_STATE["running"] = True  # 스레드가 뜨기 전에 중복 시작 방지

    def run():
        try:
            _sync()
        except NotionError:
            pass  # 에러는 SYNC_STATE["error"] 에 남음
        finally:
            SYNC_STATE["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return True


def _ensure_fresh() -> dict:
    """검색/읽기 전에 호출. 오래됐으면 백그라운드 동기화를 걸어 두고, 지금 있는 캐시로 바로 답함.
    캐시가 아예 비어 있을 때만 '받는 중'이라고 알려줌 (기다리게 하지 않음)."""
    cache = _load_cache()
    synced = cache.get("synced_at")
    if not synced or datetime.now(timezone.utc) - datetime.fromisoformat(synced) > AUTO_SYNC_AFTER:
        if not os.getenv("NOTION_TOKEN"):
            _request("GET", "/users/me")  # 토큰 없음 에러 메시지를 그대로 띄우기 위해
        start_background_sync()
    if not cache["pages"]:
        if SYNC_STATE["error"]:
            raise NotionError(SYNC_STATE["error"])
        raise NotionError(
            f"Notion 노트를 처음 내려받는 중임 ({SYNC_STATE['done']}/{SYNC_STATE['total'] or '?'}). "
            "처음 한 번은 몇 분 걸리니까 잠시 후 다시 물어봐 달라고 사용자에게 안내할 것."
        )
    return cache


def _find_page(cache: dict, title: str) -> tuple[str, dict] | None:
    """제목으로 페이지 찾기. '45' 처럼 숫자만 줘도 '45일차'를 찾도록 함."""
    t = title.strip()
    pages = cache["pages"].items()
    for pid, p in pages:
        if p["title"] == t:
            return pid, p
    if t.isdigit():
        for pid, p in pages:
            if p["title"] == f"{t}일차":
                return pid, p
    for pid, p in pages:
        if t and t in p["title"]:
            return pid, p
    return None

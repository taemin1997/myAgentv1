"""Notion 공부 비서 도구.

'SKN AI 35기 부트캠프 정리용' DB를 읽고 쓰는 도구들.

[왜 로컬 캐시를 쓰나]
Notion 공개 API의 검색(/v1/search)은 페이지 '제목'만 찾음.
노트 제목이 '45일차'처럼 날짜라서, "군집화"로 검색하면 아무것도 안 나옴.
그래서 노트 본문을 전부 받아서 data/notion_cache.json 에 저장해 두고,
검색은 그 캐시에서 직접 함. 수정 시각(last_edited_time)이 바뀐 페이지만
다시 받아오기 때문에 두 번째 동기화부터는 빠름.

[DB 구조] (Notion에서 확인한 실제 속성 이름)
  강의명(제목) / 유형(select: 강의, 실습) / 복습(checkbox) / 작성일시(created_time)

[설정] .env 에
  NOTION_TOKEN=ntn_...            ← Notion integration 토큰
  NOTION_DATA_SOURCE_ID=...       ← DB의 data source ID
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import templates
from curriculum import CURRICULUM, DATE_TO_DAY, PROJECTS
from paths import DATA_DIR
from toolkit import tool

NOTION_VERSION = "2025-09-03"  # 공식 SDK(notion-client) 최신판과 같은 API 버전
API = "https://api.notion.com/v1"
CACHE_FILE = DATA_DIR / "notion_cache.json"
AUTO_SYNC_AFTER = timedelta(minutes=30)  # 마지막 동기화 후 이만큼 지나면 검색 전에 자동 동기화

TITLE_PROP, TYPE_PROP, REVIEW_PROP = "강의명", "유형", "복습"
DATE_PROP = "날짜"  # 강의 날짜. '작성일시'(created_time)는 API로 못 바꿔서 따로 추가한 속성
TOPIC_PROP = "과목"  # 커리큘럼 기준 과목 (Python, 딥러닝, 프롬프트 엔지니어링 …)
CATEGORY_PROP = "분류"  # 페이지 종류 = DB 탭 (일차 노트 / 회고 / 프로젝트 / 면접 / 기타)


class NotionError(Exception):
    pass


# ================================================================ HTTP
def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Notion API 호출. 실패하면 사람이 이해할 수 있는 메시지로 NotionError를 던짐."""
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise NotionError("NOTION_TOKEN 이 설정되지 않았음. README의 'Notion 연결' 순서대로 토큰을 넣어야 함.")

    base = os.getenv("NOTION_BASE_URL", API)  # 테스트할 때 가짜 서버로 바꾸려고 둔 옵션
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 429 = 요청이 너무 많음. Notion은 초당 약 3회 제한이라 잠깐 쉬었다 재시도
            if e.code == 429 and attempt < 5:
                time.sleep(float(e.headers.get("Retry-After", "1")))
                continue
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise NotionError("Notion 토큰이 올바르지 않음 (401).") from e
            if e.code == 404:
                raise NotionError(
                    "Notion에서 DB/페이지를 못 찾음 (404). DB 페이지 오른쪽 위 ••• → '연결'에서 "
                    "만든 integration을 추가했는지 확인해야 함."
                ) from e
            raise NotionError(f"Notion API 오류 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise NotionError(f"Notion 서버에 연결 못 함: {e.reason}") from e
    raise NotionError("Notion 요청이 계속 거절됨 (429). 잠시 후 다시 시도해야 함.")


def _data_source_id() -> str:
    ds = os.getenv("NOTION_DATA_SOURCE_ID")
    if not ds:
        raise NotionError("NOTION_DATA_SOURCE_ID 가 설정되지 않았음.")
    return ds


# ================================================================ Notion 블록 → 텍스트
def _plain(rich_text: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


def _block_to_text(block: dict) -> str:
    """블록 하나를 마크다운 비슷한 한 줄(또는 여러 줄) 텍스트로 바꿈."""
    t = block["type"]
    v = block.get(t, {})
    text = _plain(v.get("rich_text", []))
    prefix = {
        "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
        "bulleted_list_item": "- ", "numbered_list_item": "1. ", "quote": "> ",
        "callout": "💡 ", "toggle": "▸ ",
    }
    if t in prefix:
        return prefix[t] + text
    if t == "to_do":
        return f"[{'x' if v.get('checked') else ' '}] {text}"
    if t == "code":
        return f"```{v.get('language', '')}\n{text}\n```"
    if t == "table_row":
        # 마크다운 표 모양 그대로 (| a | b |). 모델이 읽은 걸 고쳐서 다시 쓸 때 표로 되돌아가게
        return "| " + " | ".join(_plain(cell) for cell in v.get("cells", [])) + " |"
    if t == "child_page":
        return f"(하위 페이지: {v.get('title', '')})"
    if t in ("divider",):
        return "---"
    return text  # paragraph 등. 이미지/파일 같은 건 텍스트가 없어서 빈 문자열


def _page_text(block_id: str, depth: int = 0) -> str:
    """페이지(또는 블록)의 모든 자식 블록을 읽어서 텍스트로 합침.
    토글 안 내용처럼 중첩된 블록은 has_children 이 true 라서 재귀로 내려감."""
    lines, cursor = [], None
    while True:
        q = f"?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        res = _request("GET", f"/blocks/{block_id}/children{q}")
        for b in res["results"]:
            line = _block_to_text(b)
            if line:
                lines.append("  " * depth + line)
            # 하위 '페이지'는 별도 노트일 수 있어서 들어가지 않음 (너무 깊어지는 것 방지)
            if b.get("has_children") and b["type"] != "child_page" and depth < 3:
                child = _page_text(b["id"], depth + 1)
                if child:
                    lines.append(child)
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    return "\n".join(lines)


# ================================================================ 캐시 & 동기화
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


# ================================================================ 마크다운 → Notion 블록 (저장용)
def _rich(text: str) -> list:
    """'**굵게**', '`코드`', '[글자](링크)' 만 처리. Notion은 텍스트 조각 하나에 2000자 제한이 있어서 잘라 넣음."""
    parts = []
    for chunk in re.split(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?://[^)\s]+\))", text):
        if not chunk:
            continue
        ann, link = {}, None
        if chunk.startswith("**") and chunk.endswith("**"):
            chunk, ann = chunk[2:-2], {"bold": True}
        elif chunk.startswith("`") and chunk.endswith("`"):
            chunk, ann = chunk[1:-1], {"code": True}
        elif m := re.fullmatch(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", chunk):
            chunk, link = m.group(1), m.group(2)
        for j in range(0, len(chunk), 2000):
            item = {"type": "text", "text": {"content": chunk[j:j + 2000]}}
            if link:
                item["text"]["link"] = {"url": link}
            if ann:
                item["annotations"] = ann
            parts.append(item)
    return parts


def _block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rich(text), **extra}}


def _is_table_sep(line: str) -> bool:
    """'|---|---|' 또는 '--- | ---' 같은 표 구분줄인지."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return len(cells) > 1 and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def _markdown_to_blocks(md: str) -> list[dict]:
    blocks, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):  # 코드 블록: 닫는 ``` 까지 모아서 하나로
            lang = s[3:].strip().lower() or "plain text"
            code, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append({"object": "block", "type": "code",
                           "code": {"rich_text": [{"type": "text", "text": {"content": c}}
                                                  for c in _chunks("\n".join(code))],
                                    "language": lang if lang in _CODE_LANGS else "plain text"}})
        elif s.startswith("|") or ("|" in s and i + 1 < len(lines) and _is_table_sep(lines[i + 1])):
            # 표: '| a | b |' 줄들, 또는 바깥 | 없이 'a | b' + 다음 줄 '--- | ---' 인 경우도 표로 봄
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):  # |---|---| 구분줄은 건너뜀
                    rows.append(cells)
                i += 1
            i -= 1  # 아래 i += 1 과 맞추려고
            width = max(len(r) for r in rows)
            blocks.append({"object": "block", "type": "table", "table": {
                "table_width": width, "has_column_header": True, "has_row_header": False,
                # Notion 표는 모든 행의 칸 수가 같아야 해서 모자란 칸은 빈칸으로 채움
                "children": [{"type": "table_row", "table_row": {
                    "cells": [_rich(c) for c in r + [""] * (width - len(r))]}} for r in rows]}})
        elif m := re.match(r"^(#{1,6})\s+(.*)", s):
            # Notion 제목은 3단계까지라서 ####, ##### 등은 전부 heading_3 으로
            blocks.append(_block(f"heading_{min(len(m.group(1)), 3)}", m.group(2)))
        elif m := re.match(r"^[-*] \[( |x|X)\] ?(.*)", s):  # 체크박스: - [ ] 할 일 / - [x] 끝난 일
            blocks.append(_block("to_do", m.group(2), checked=m.group(1) != " "))
        elif s.startswith(("- ", "* ")):
            blocks.append(_block("bulleted_list_item", s[2:]))
        elif re.match(r"^\d+\. ", s):
            blocks.append(_block("numbered_list_item", s.split(" ", 1)[1]))
        elif s.startswith("> "):
            blocks.append(_block("quote", s[2:]))
        elif s == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif s:
            blocks.append(_block("paragraph", s))
        i += 1
    return blocks


def _chunks(text: str, size: int = 2000) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


_CODE_LANGS = {"python", "javascript", "typescript", "sql", "bash", "shell", "json", "html", "css",
               "java", "c", "c++", "markdown", "yaml", "plain text"}


# ================================================================ 도구들
@tool
def sync_study_notes() -> str:
    """Notion 공부 노트를 다시 불러오기 시작합니다 (백그라운드). 사용자가 '방금 노션에 정리했어' 처럼
    최신 내용을 반영해 달라고 할 때만 쓰세요. (검색/읽기 도구는 30분마다 자동 동기화됨)
    """
    if not os.getenv("NOTION_TOKEN"):
        return "NOTION_TOKEN 이 설정되지 않았음. 창 오른쪽 위 Notion 버튼으로 토큰을 넣어야 함."
    if SYNC_STATE["running"]:
        return f"이미 동기화 중임 ({SYNC_STATE['done']}/{SYNC_STATE['total'] or '?'})."
    start_background_sync()
    return "동기화를 시작함. 바뀐 노트만 받으니 보통 몇 초~수십 초면 끝남. 창 아래에 진행 상황이 표시됨."


@tool
def search_study_notes(keywords: str) -> str:
    """부트캠프 공부 노트(Notion) 본문에서 키워드로 검색합니다. 사용자가 배운 내용,
    예전에 정리한 개념, 'OO 정리한 거 찾아줘' 같은 질문을 하면 먼저 이걸 쓰세요.
    결과의 제목으로 read_study_note를 호출하면 전체 내용을 볼 수 있습니다.

    Args:
        keywords: 띄어쓰기로 구분한 검색어. 한국어와 영어 표현을 같이 넣으면 잘 찾음. 예: 군집화 KMeans clustering.
    """
    try:
        cache = _ensure_fresh()
    except NotionError as e:
        return str(e)
    words = [w.lower() for w in keywords.split() if w.strip()]
    if not words:
        return "검색어가 비어 있음."

    scored = []
    for p in cache["pages"].values():
        hay = (p["title"] + "\n" + p["text"]).lower()
        counts = [hay.count(w) for w in words]
        # 검색어를 '몇 종류' 포함하는지가 먼저, 같으면 많이 언급된 순
        # (흔한 단어 하나가 수십 번 나오는 노트가 핵심 단어를 가진 노트를 이기지 않게)
        score = (sum(1 for c in counts if c) * 10_000) + sum(counts)
        if sum(counts):
            scored.append((score, p))
    if not scored:
        return f"'{keywords}' 관련 노트를 못 찾음. 다른 표현(영어 용어 등)으로 다시 검색해 볼 것."

    out = []
    for score, p in sorted(scored, key=lambda x: -x[0])[:6]:
        text = p["text"]
        pos = max((text.lower().find(w) for w in words), default=-1)
        snippet = text[max(0, pos - 60): pos + 140].replace("\n", " ") if pos >= 0 else text[:150]
        out.append(f"- {p['title']} ({p['created']}, {p['type'] or '-'}, 언급 {score % 10_000}회): …{snippet}…")
    return "\n".join(out)


@tool
def read_study_note(title: str) -> str:
    """공부 노트 한 개의 전체 내용을 읽습니다. 요약, 설명, 복습 퀴즈를 만들기 전에 사용하세요.

    Args:
        title: 노트 제목. 예: 45일차 (숫자만 써도 됨).
    """
    try:
        cache = _ensure_fresh()
    except NotionError as e:
        return str(e)
    found = _find_page(cache, title)
    if not found:
        return f"'{title}' 노트가 없음. list_study_notes로 제목을 확인할 것."
    _, p = found
    text = p["text"] or "(본문이 비어 있음. 첨부 파일이나 이미지만 있는 노트일 수 있음)"
    if len(text) > 12000:  # 너무 길면 모델 입력이 커지니까 앞부분만
        text = text[:12000] + "\n…(이하 생략)"
    return f"# {p['title']} ({p['created']}, {p['type'] or '-'}, 복습 {'완료' if p['reviewed'] else '안 함'})\n{p['url']}\n\n{text}"


@tool
def list_study_notes(only_not_reviewed: bool = False, limit: int = 20) -> str:
    """공부 노트 목록을 최신순으로 보여줍니다. '복습 안 한 거 뭐 있어?', '최근에 뭐 배웠지?' 같은 질문에 쓰세요.

    Args:
        only_not_reviewed: true면 아직 복습 체크 안 한 노트만.
        limit: 최대 몇 개 보여줄지.
    """
    try:
        cache = _ensure_fresh()
    except NotionError as e:
        return str(e)
    pages = sorted(cache["pages"].values(), key=lambda p: p["created"], reverse=True)
    if only_not_reviewed:
        pages = [p for p in pages if not p["reviewed"]]
    if not pages:
        return "해당하는 노트가 없음."
    lines = [
        f"- {p['title']} ({p['created']}, {p['type'] or '-'}) {'✅' if p['reviewed'] else '⬜ 복습 전'}"
        + ("" if p["text"].strip() else " [본문 비어 있음]")
        for p in pages[:limit]
    ]
    return f"전체 {len(pages)}개 중 {min(limit, len(pages))}개:\n" + "\n".join(lines)


def guess_category(title: str) -> str:
    """제목으로 Notion '분류'를 추측. 'N일차' → 일차 노트, '회고' → 회고, 그 외 → 기타."""
    t = title.strip()
    if re.fullmatch(r"\d+일차", t):
        return "일차 노트"
    if "회고" in t:
        return "회고"
    if "면접" in t:
        return "면접"
    if "프로젝트" in t:
        return "프로젝트"
    return "기타"


def create_note(title: str, content: str, note_type: str = "강의", date: str = "",
                category: str = "", topic: str = "") -> dict:
    """노트를 Notion DB에 만든다. 채팅 도구(save_study_note)와 GUI 정리 카드가 같이 씀.
    category(분류)를 비우면 제목으로 추측함 (탭에서 바로 보이게 하려고 항상 채움).
    실패하면 NotionError. 성공하면 {"url", "blocks"}."""
    blocks = _markdown_to_blocks(content)
    props = {TITLE_PROP: {"title": _rich(title)}}
    if note_type in ("강의", "실습"):
        props[TYPE_PROP] = {"select": {"name": note_type}}
    props[CATEGORY_PROP] = {"select": {"name": category or guess_category(title)}}
    # 'N일차' 노트면 커리큘럼에서 과목을 채우고, 날짜를 안 줬으면 커리큘럼 날짜를 씀
    if (m := re.fullmatch(r"(\d+)일차", title.strip())) and int(m.group(1)) in CURRICULUM:
        cur_date, _course, cur_topic = CURRICULUM[int(m.group(1))]
        topic = topic or cur_topic
        date = date or cur_date
    if topic:  # 일차 노트는 커리큘럼에서, 프로젝트 페이지 등은 넘겨받은 값으로 (예: 최종 프로젝트)
        props[TOPIC_PROP] = {"select": {"name": topic}}
    if date:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise NotionError(f"날짜 형식이 이상함: {date} (YYYY-MM-DD 로 써야 함)")
        props[DATE_PROP] = {"date": {"start": date}}
    # Notion은 한 번에 블록 100개까지만 받음 → 처음 100개는 생성 때, 나머지는 이어 붙임
    page = _request("POST", "/pages", {
        "parent": {"type": "data_source_id", "data_source_id": _data_source_id()},
        "properties": props,
        "children": blocks[:100],
    })
    for i in range(100, len(blocks), 100):
        _request("PATCH", f"/blocks/{page['id']}/children", {"children": blocks[i:i + 100]})
    start_background_sync()  # 방금 저장한 노트도 곧 검색되게 (기다리지 않음)
    return {"url": page.get("url", ""), "blocks": len(blocks), "id": page["id"]}


def suggest_next_days(count: int) -> list[tuple[str, str]]:
    """다음에 만들 노트의 (제목, 날짜) 제안. 예: [('87일차', '2026-11-12'), ('88일차', '2026-11-13')]

    - 번호: 캐시에 있는 'N일차' 중 가장 큰 N 다음부터
    - 날짜: 커리큘럼(curriculum.py)의 그 일차 날짜. 커리큘럼 범위 밖이면 다음 평일로 추정
    """
    pages = _load_cache()["pages"].values()
    days = [(int(m.group(1)), p["created"]) for p in pages if (m := re.fullmatch(r"(\d+)일차", p["title"].strip()))]
    last_n, last_date = max(days) if days else (0, datetime.now().strftime("%Y-%m-%d"))
    d = datetime.strptime(last_date, "%Y-%m-%d")
    out = []
    for i in range(1, count + 1):
        n = last_n + i
        if n in CURRICULUM:
            d = datetime.strptime(CURRICULUM[n][0], "%Y-%m-%d")
        else:
            d += timedelta(days=1)
            while d.weekday() >= 5:  # 토(5), 일(6) 건너뜀
                d += timedelta(days=1)
        out.append((f"{n}일차", d.strftime("%Y-%m-%d")))
    return out


def suggest_titles(title_rule: str, count: int) -> list[tuple[str, str]]:
    """템플릿의 제목 규칙에 맞는 (제목, 날짜) 제안.
    'N일차' → suggest_next_days, 'N주차 회고' → 마지막 회고 번호 + 1 (날짜는 오늘)."""
    if "일차" in title_rule:
        return suggest_next_days(count)
    if "주차" not in title_rule:  # 프로젝트 페이지처럼 제목이 자유인 템플릿
        return [("", datetime.now().strftime("%Y-%m-%d"))] * count
    pages = _load_cache()["pages"].values()
    weeks = [int(m.group(1)) for p in pages if (m := re.match(r"(\d+)주차", p["title"].strip()))]
    last = max(weeks, default=0)
    today = datetime.now().strftime("%Y-%m-%d")
    return [(f"{last + i}주차 회고", today) for i in range(1, count + 1)]


@tool
def get_curriculum(query: str = "") -> str:
    """부트캠프 커리큘럼(1~120일차, 2026-07-07 ~ 12-30)과 단위·최종 프로젝트 정보를 알려줍니다.
    '다음 주에 뭐 배워?', '딥러닝 몇 일차였지?', '2차 프로젝트 뭐였지?', '수료까지 며칠 남았어?' 같은 질문에 쓰세요.

    Args:
        query: 일차 번호(예: 45), 날짜(예: 2026-11-16), 과목 키워드(예: 딥러닝), 또는 빈 문자열(전체 요약).
    """
    q = query.strip().replace("일차", "")
    if q.isdigit() and int(q) in CURRICULUM:
        date, course, topic = CURRICULUM[int(q)]
        return f"{q}일차: {date} / {course} / {topic}"
    if q in DATE_TO_DAY:
        n = DATE_TO_DAY[q]
        return f"{q}은(는) {n}일차: {CURRICULUM[n][1]} / {CURRICULUM[n][2]}"
    if q:
        # 과목명(딥러닝)에서 먼저 찾고, 없으면 교과목명(데이터 분석과 머신러닝, 딥러닝)에서 찾음
        hits = ([(n, *v) for n, v in CURRICULUM.items() if q.lower() == v[2].lower()]
                or [(n, *v) for n, v in CURRICULUM.items() if q.lower() in v[2].lower()]
                or [(n, *v) for n, v in CURRICULUM.items() if q.lower() in v[1].lower()])
        if hits:
            return (f"'{query}' 관련 {len(hits)}일: {hits[0][0]}~{hits[-1][0]}일차 "
                    f"({hits[0][1]} ~ {hits[-1][1]})\n" + "\n".join(f"- {n}일차 {d} {t}" for n, d, _, t in hits[:40]))
    # 전체 요약: 과목이 바뀌는 지점마다 묶기
    lines, start = [], 1
    for n in range(2, 122):
        if n > 120 or CURRICULUM[n][2] != CURRICULUM[start][2]:
            s, e = CURRICULUM[start], CURRICULUM[n - 1]
            lines.append(f"- {start}~{n - 1}일차 ({s[0][5:]}~{e[0][5:]}): {s[2]} [{s[1]}]")
            start = n
    return "커리큘럼 요약:\n" + "\n".join(lines) + "\n\n프로젝트:\n" + PROJECTS


_CREATED: dict[str, str] = {}  # 이번 실행에서 만든 페이지 제목 → url (중복 생성 방지)


@tool
def save_study_note(title: str, content: str, note_type: str = "강의", date: str = "",
                    category: str = "", topic: str = "") -> str:
    """Notion 부트캠프 DB에 새 페이지를 만듭니다. 일차 노트뿐 아니라 회고, 프로젝트 정리 페이지,
    면접 질문 페이지 등 DB에 들어가는 모든 종류의 페이지를 만들 수 있음. 내용이 아직 없으면
    '(작성 예정)' 칸만 있는 빈 틀(템플릿) 페이지로 만들어도 됨.
    만들기 전에 get_note_template으로 맞는 템플릿(일차 노트=강의노트/회의록, 프로젝트=프로젝트, 회고=주간회고)을
    불러서 그 섹션 구조를 그대로 따르세요. 내용이 있는 노트는 먼저 보여주고 확인을 받은 뒤 저장하세요.

    Args:
        title: 페이지 제목. 일차 노트는 'N일차'(예: 89일차), 회고는 'N주차 회고', 프로젝트는 '최종 프로젝트 — 이름'처럼.
        content: 마크다운 본문. #, ##, -, 1., ```코드```, **굵게**, [링크](url) 지원. 반말체(~임/~함)로 작성.
        note_type: 강의 또는 실습. 회고·프로젝트처럼 해당 없으면 빈 문자열.
        date: 날짜 YYYY-MM-DD. 모르면 빈 문자열.
        category: 분류(DB 탭). 일차 노트/회고/프로젝트/면접/기타. 템플릿을 썼으면 템플릿의 분류. 비우면 제목으로 추측.
        topic: 과목. 'N일차'면 자동이라 비워 둠. 프로젝트 페이지는 '단위 프로젝트' 또는 '최종 프로젝트'.
    """
    # 같은 제목 페이지가 이미 있으면 새로 만들지 않음 (모델이 "응 저장해"에 한 번 더 저장하는 걸 막음).
    # 방금 만든 페이지는 아직 캐시에 없을 수 있어서 이번 실행에서 만든 제목도 같이 봄
    t = title.strip()
    existing = _CREATED.get(t) or next(
        (p["url"] for p in _load_cache()["pages"].values() if p["title"].strip() == t), None)
    if existing:
        return (f"'{t}' 페이지가 이미 있어서 새로 만들지 않음: {existing}\n"
                "이미 저장된 거라고 사용자에게 알려줄 것. 정말 하나 더 필요하면 제목을 바꿔서 저장.")
    try:
        r = create_note(title, content, note_type, date, category, topic)
        _CREATED[t] = r["url"] or "(방금 만듦)"
        _record({"kind": "create", "page_id": r["id"], "page_title": t})  # 되돌리기 때 휴지통으로
        return f"'{title}' 노트를 Notion에 저장함 ({r['blocks']}블록). {r['url']}"
    except NotionError as e:
        return str(e)


@tool
def mark_note_reviewed(title: str) -> str:
    """노트의 '복습' 체크박스를 체크합니다. 사용자가 복습을 끝냈다고 하거나 퀴즈를 다 풀었을 때 쓰세요.

    Args:
        title: 노트 제목. 예: 45일차.
    """
    try:
        cache = _ensure_fresh()
        found = _find_page(cache, title)
        if not found:
            return f"'{title}' 노트가 없음."
        pid, p = found
        page = _request("PATCH", f"/pages/{pid}", {"properties": {REVIEW_PROP: {"checkbox": True}}})
        p["reviewed"], p["edited"] = True, page.get("last_edited_time", p["edited"])
        _save_cache(cache)
        return f"'{p['title']}' 복습 완료 체크함 ✅"
    except NotionError as e:
        return str(e)


def _children(block_id: str) -> list[dict]:
    """블록의 바로 아래 자식 블록 전부 (페이지네이션 처리)."""
    out, cursor = [], None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        res = _request("GET", f"/blocks/{block_id}/children{q}")
        out += res["results"]
        if not res.get("has_more"):
            return out
        cursor = res["next_cursor"]


def _norm(text: str) -> str:
    """'## 1. 개요 📌' 와 '개요' 를 같게 보려고 숫자·기호·공백·이모지를 지움."""
    return re.sub(r"[\W\d_]+", "", text).lower()


@tool
def update_page_section(title: str, section: str, content: str, mode: str = "replace") -> str:
    """이미 있는 Notion 페이지에서 '## 섹션' 하나의 내용만 바꾸거나 이어 씁니다. 페이지 전체를 다시 만들지 않음.
    최종 프로젝트 페이지처럼 계속 갱신하는 페이지에 쓰세요. 고치기 전에 read_study_note로 현재 내용을 읽고,
    어떤 섹션을 어떻게 바꿀지 사용자에게 보여주고 확인받은 뒤 쓰세요.

    Args:
        title: 페이지 제목. 예: 최종 프로젝트 — Compare Agent 2.1
        section: 바꿀 섹션 제목 (번호·이모지 없이 일부만 써도 됨). 예: 기술 스택, 진행 기록. 없으면 페이지 끝에 새로 만듦.
        content: 섹션 제목 아래에 들어갈 마크다운 (섹션 제목 줄 자체는 빼고). 표 | a | b |, 체크박스 - [ ] 지원.
        mode: replace = 섹션 내용을 통째로 교체 (기술 스택·시스템 구조처럼 최신 상태만 필요한 칸),
              append = 섹션 끝에 이어 쓰기 (진행 기록·트러블슈팅처럼 날마다 쌓이는 칸).
    """
    try:
        found = _find_page(_load_cache(), title)
        if not found or found[1]["title"].strip() != title.strip():
            return f"'{title}' 페이지를 못 찾음. 제목을 정확히 확인하거나 sync_study_notes 후 다시 시도."
        pid = found[0]
        blocks = _children(pid)
        new = _markdown_to_blocks(content)
        if not new:
            return "넣을 내용이 비어 있음."
        heads = [(i, b) for i, b in enumerate(blocks) if b["type"] in ("heading_1", "heading_2", "heading_3")]
        target = next(((i, b) for i, b in heads if _norm(section) and _norm(section) in _norm(_plain(b[b["type"]]["rich_text"]))), None)

        page_title = found[1]["title"]
        if target is None:  # 없는 섹션 → 페이지 끝에 '## 섹션' + 내용
            new_ids = _append(pid, [_block("heading_2", section)] + new)
            _record({"kind": "section", "page_id": pid, "page_title": page_title, "section": section,
                     "mode": "new", "old_blocks": [], "anchor": None, "new_ids": new_ids})
            start_background_sync()
            return f"'{section}' 섹션이 없어서 페이지 끝에 새로 만듦 ({len(new)}블록)."

        idx, head = target
        level = int(head["type"][-1])
        # 섹션 끝 = 다음에 나오는 같은 단계 이상 제목 (## 섹션이면 다음 # 또는 ##)
        end = next((i for i, b in heads if i > idx and int(b["type"][-1]) <= level), len(blocks))
        body = blocks[idx + 1:end]
        # 섹션 끝의 구분선(---)은 다음 섹션과의 경계라서 지우지도, 그 뒤에 붙이지도 않음
        while body and body[-1]["type"] == "divider":
            body = body[:-1]
        head_text = _plain(head[head["type"]]["rich_text"])
        if mode == "append":
            anchor, old = (body[-1]["id"] if body else head["id"]), []
        else:
            # 지우기 전에 원래 블록을 (하위 블록까지) 저장 → undo_last_update 로 되살릴 수 있게
            old = [s for b in body if (s := _serialize(b))]
            for b in body:
                _request("DELETE", f"/blocks/{b['id']}")
            anchor = head["id"]
        new_ids = _append(pid, new, after=anchor)
        _record({"kind": "section", "page_id": pid, "page_title": page_title, "section": head_text,
                 "mode": "append" if mode == "append" else "replace",
                 "old_blocks": old, "anchor": head["id"], "new_ids": new_ids})
        start_background_sync()
        return (f"'{page_title}' 페이지의 '{head_text}' 섹션 "
                + (f"교체함 (기존 {len(body)}블록 → 새 {len(new)}블록, 원래 내용은 백업함)."
                   if mode != "append" else f"끝에 {len(new)}블록 추가함."))
    except NotionError as e:
        return str(e)


def _append(page_id: str, blocks: list[dict], after: str | None = None) -> list[str]:
    """블록 추가. after 를 주면 그 블록 바로 뒤에 끼워 넣음. Notion은 한 번에 100개까지라 나눠서 보냄.
    새로 생긴 최상위 블록 id 목록을 돌려줌 (되돌리기 때 이것만 지우면 됨)."""
    # 주의: Notion 응답의 results 에는 새 블록만 오는 게 아니라 (after 로 끼워 넣으면) 그 뒤 블록들까지 섞여 옴.
    # 그걸 새 블록으로 기록하면 되돌리기 때 멀쩡한 뒤쪽 섹션까지 지워짐 (테스트에서 실제로 발생).
    # → 넣기 전·후의 자식 블록 id 목록을 비교해서 '정말 새로 생긴 것'만 골라냄.
    ids = []
    for i in range(0, len(blocks), 100):
        chunk = blocks[i:i + 100]
        before = {b["id"] for b in _children(page_id)}
        body = {"children": chunk}
        if after:
            body["after"] = after
        _request("PATCH", f"/blocks/{page_id}/children", body)
        got = [b["id"] for b in _children(page_id) if b["id"] not in before]
        if len(got) != len(chunk):
            raise NotionError(f"블록 추가 결과가 이상함 (넣은 {len(chunk)}개, 새로 보인 {len(got)}개)")
        ids += got
        if after:
            after = got[-1]  # 다음 묶음은 방금 넣은 마지막 블록 뒤에 이어 붙임
    return ids


# ================================================================ 백업 & 되돌리기
# 앱이 Notion을 고칠 때마다 '무엇을 지우고 무엇을 넣었는지'를 data/update_history.json 에 남김.
# 같은 질문(run_turn 한 번) 안에서 한 수정은 같은 batch 로 묶어서, "방금 갱신 되돌려줘" 한 번에 다 되돌림.
HISTORY_FILE = DATA_DIR / "update_history.json"
HISTORY_KEEP = 40  # 오래된 기록은 버림
CURRENT_BATCH = {"id": "manual"}  # agent.run_turn 이 질문마다 새 id로 바꿈


def begin_batch() -> None:
    CURRENT_BATCH["id"] = datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _history() -> list[dict]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def _record(entry: dict) -> None:
    hist = _history()
    hist.append({"batch": CURRENT_BATCH["id"], "time": datetime.now().strftime("%m/%d %H:%M"), **entry})
    DATA_DIR.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(hist[-HISTORY_KEEP:], ensure_ascii=False), encoding="utf-8")


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
    rest = [e for e in hist if e["batch"] != batch]
    HISTORY_FILE.write_text(json.dumps(rest, ensure_ascii=False), encoding="utf-8")
    start_background_sync()
    when = todo[0]["time"]
    return (f"{when}에 한 수정 {len(todo)}건을 되돌림:\n" + "\n".join(f"- {d}" for d in done)
            + ("\n문제:\n" + "\n".join(f"- {p}" for p in problems) if problems else ""))


@tool
def get_note_template(name: str = "") -> str:
    """저장해 둔 노트 정리 방식(템플릿)을 불러옵니다. 자료를 정리하기 전에 꼭 먼저 불러서 그 형식대로 정리하세요.
    이름을 비우면 템플릿 목록을 보여줍니다.

    Args:
        name: 템플릿 이름. 예: 강의노트, 회의록, 주간회고. 빈 문자열이면 목록.
    """
    all_t = templates.load_templates()
    t = all_t.get(name.strip())
    if not t:
        listing = "\n".join(f"- {x.name}: {x.description} (분류 {x.category}, 제목 {x.title_rule})"
                            for x in all_t.values())
        return ("" if not name else f"'{name}' 템플릿은 없음.\n") + "템플릿 목록:\n" + listing
    return (f"[{t.name}] 분류={t.category}, 유형={t.note_type or '없음'}, 과목={t.topic or '자동/없음'}, "
            f"제목 규칙={t.title_rule}\n"
            f"저장할 때 save_study_note(category='{t.category}', note_type='{t.note_type}', topic='{t.topic}')로 저장할 것.\n\n"
            + t.system_prompt())


@tool
def save_note_template(name: str, body: str, description: str = "", category: str = "",
                       note_type: str = "", title_rule: str = "", topic: str = "") -> str:
    """노트 정리 방식(템플릿)을 새로 만들거나 고쳐서 저장합니다. 다음부터 같은 방식으로 정리할 수 있게 됨.
    사용자가 '앞으로 이렇게 정리해줘', '이 형식 기억해'라고 하면, 바꿀 내용을 보여주고 확인받은 뒤 쓰세요.
    기존 템플릿을 고칠 땐 먼저 get_note_template으로 읽고, 바뀐 부분을 반영한 전체 본문을 넘기세요.

    Args:
        name: 템플릿 이름. 예: 회의록, 세미나정리.
        body: 정리 지시문 전체 (구조, 섹션, 주의할 점). 말투 규칙은 자동으로 붙으니 안 써도 됨.
        description: 언제 쓰는 템플릿인지 한 줄. 비우면 기존 값 유지.
        category: Notion 분류. 일차 노트/회고/프로젝트/면접/기타. 비우면 기존 값 유지.
        note_type: 강의/실습/빈 문자열.
        title_rule: 제목 규칙. 'N일차', 'N주차 회고', '자유' 중 하나. 비우면 기존 값 유지.
        topic: Notion 과목. N일차는 자동이라 비움. 프로젝트 페이지면 '최종 프로젝트' 등.
    """
    try:
        t = templates.save_template(name, body, description, category, note_type, title_rule, topic)
    except (ValueError, OSError) as e:
        return f"템플릿 저장 실패: {e}"
    return f"'{t.name}' 템플릿 저장함 (분류 {t.category}, 제목 {t.title_rule}). 파일: prompts/{t.name}.md"


NOTION_TOOLS = [
    update_page_section,
    undo_last_update,
    get_note_template,
    save_note_template,
    get_curriculum,
    search_study_notes,
    read_study_note,
    list_study_notes,
    save_study_note,
    mark_note_reviewed,
    sync_study_notes,
]

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
        return " | ".join(_plain(cell) for cell in v.get("cells", []))
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
        elif m := re.match(r"^(#{1,6})\s+(.*)", s):
            # Notion 제목은 3단계까지라서 ####, ##### 등은 전부 heading_3 으로
            blocks.append(_block(f"heading_{min(len(m.group(1)), 3)}", m.group(2)))
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


def create_note(title: str, content: str, note_type: str = "강의", date: str = "") -> dict:
    """노트를 Notion DB에 만든다. 채팅 도구(save_study_note)와 GUI 정리 카드가 같이 씀.
    실패하면 NotionError. 성공하면 {"url", "blocks"}."""
    blocks = _markdown_to_blocks(content)
    props = {TITLE_PROP: {"title": _rich(title)}}
    if note_type in ("강의", "실습"):
        props[TYPE_PROP] = {"select": {"name": note_type}}
    # 'N일차' 노트면 커리큘럼에서 과목을 채우고, 날짜를 안 줬으면 커리큘럼 날짜를 씀
    if (m := re.fullmatch(r"(\d+)일차", title.strip())) and int(m.group(1)) in CURRICULUM:
        cur_date, _course, topic = CURRICULUM[int(m.group(1))]
        props[TOPIC_PROP] = {"select": {"name": topic}}
        date = date or cur_date
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
    return {"url": page.get("url", ""), "blocks": len(blocks)}


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


@tool
def save_study_note(title: str, content: str, note_type: str = "강의", date: str = "") -> str:
    """정리한 공부 내용을 Notion 부트캠프 DB에 새 노트로 저장합니다.
    저장하기 전에 사용자에게 제목과 내용을 보여주고 확인을 받으세요.

    Args:
        title: 노트 제목. 기존 규칙대로 'N일차' 형식을 권장. 예: 75일차.
        content: 마크다운 본문. #, ##, -, 1., ```코드```, **굵게** 지원. 반말체(~임/~함)로 작성.
        note_type: 강의 또는 실습.
        date: 강의 날짜 YYYY-MM-DD. 모르면 빈 문자열.
    """
    try:
        r = create_note(title, content, note_type, date)
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


NOTION_TOOLS = [
    get_curriculum,
    search_study_notes,
    read_study_note,
    list_study_notes,
    save_study_note,
    mark_note_reviewed,
    sync_study_notes,
]

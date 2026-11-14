"""Notion 공부 비서 도구 (모델이 부르는 함수들) + GUI가 쓰는 노트 생성·제목 제안.

'SKN AI 35기 부트캠프 정리용' DB를 읽고 쓰는 도구들. 하위 모듈:
  client(요청) → blocks(블록 변환) → cache(동기화) → history(되돌리기) → tools(여기)
"""

import os
import re
from datetime import datetime, timedelta

from core.toolkit import tool
from study import templates
from study.curriculum import CURRICULUM, DATE_TO_DAY, PROJECTS

from .blocks import _append, _block, _children, _markdown_to_blocks, _plain, _rich
from .cache import SYNC_STATE, _ensure_fresh, _find_page, _load_cache, _save_cache, start_background_sync
from .client import (CATEGORY_PROP, DATE_PROP, REVIEW_PROP, TITLE_PROP, TOPIC_PROP, TYPE_PROP,
                     NotionError, _data_source_id, _request)
from .history import _CREATED, _record, _serialize, undo_last_update


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

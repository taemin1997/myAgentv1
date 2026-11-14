"""Notion 블록 ↔ 텍스트.

- 읽기: 블록 → 마크다운 비슷한 텍스트 (검색용 캐시, 모델이 읽는 본문)
- 쓰기: 마크다운 → 블록 (노트 저장, 섹션 갱신). 표·체크박스·코드 블록 지원
- 블록 목록 받기(_children) / 끼워 넣기(_append)
"""

import re

from .client import NotionError, _request


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

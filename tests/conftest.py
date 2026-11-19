"""테스트 공통 준비: 진짜 data/ 파일·Notion·GitHub·OpenAI 를 절대 건드리지 않게 막고, 가짜를 끼워 넣음.

[가짜를 바꿔 끼우는 위치]
`from .client import _request` 처럼 이름을 가져다 쓰는 모듈이 많아서, client 모듈 하나만 바꾸면 소용없음.
→ 그 이름을 쓰는 모듈 쪽 이름을 전부 바꿈 (CLAUDE.md '개발 환경 메모' 참고).
"""

import itertools
import os
from datetime import datetime, timezone

import pytest

from core import usage
import tools.github as github
import tools.life as life
import tools.notion.blocks as nblocks
import tools.notion.cache as ncache
import tools.notion.client as nclient
import tools.notion.history as nhistory
import tools.notion.tools as ntools

_REQUEST_USERS = (nclient, nblocks, ncache, nhistory, ntools)
_SYNC_USERS = (ncache, nhistory, ntools)


def _blocked(*args, **kwargs):
    raise AssertionError("테스트에서 실제 네트워크 요청을 하려고 함 (가짜를 안 끼웠음)")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """모든 테스트에 자동 적용: data/ 파일을 임시 폴더로, 네트워크 요청은 막고, 백그라운드 동기화는 끔."""
    monkeypatch.setattr(ncache, "CACHE_FILE", tmp_path / "notion_cache.json")
    monkeypatch.setattr(nhistory, "HISTORY_FILE", tmp_path / "update_history.json")
    monkeypatch.setattr(life, "NOTES_FILE", tmp_path / "notes.json")
    monkeypatch.setattr(life, "TODOS_FILE", tmp_path / "todos.json")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(usage, "SESSION", {"input": 0, "cached": 0, "output": 0, "cost": 0.0, "calls": 0})
    for key in [k for k in os.environ if k.startswith("PRICE_")]:  # 내 .env 의 가격 설정이 테스트에 끼지 않게
        monkeypatch.delenv(key)
    for mod in _REQUEST_USERS:
        monkeypatch.setattr(mod, "_request", _blocked)
    for mod in _SYNC_USERS:
        monkeypatch.setattr(mod, "start_background_sync", lambda: False)
    monkeypatch.setattr(github, "_get", _blocked)
    monkeypatch.setattr(github, "_post", _blocked)  # 이슈 쓰기는 특히 진짜로 나가면 팀원에게 알림이 감
    monkeypatch.setattr(github, "ISSUE_DRAFT_HANDLER", None)  # 웹 Api 가 바꿔 놓은 게 다음 테스트로 안 새게
    monkeypatch.setattr(github, "ISSUE_REMOVAL_HANDLER", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)  # 내 .env 토큰 유무가 테스트 결과를 바꾸지 않게
    monkeypatch.setenv("NOTION_TOKEN", "test-token-not-real")
    nhistory._CREATED.clear()
    return tmp_path


# ====================================================================== 가짜 Notion
class FakeNotion:
    """블록 목록 GET·끼워 넣기·지우기, 페이지 만들기·속성 바꾸기만 흉내 냄.

    진짜 Notion 의 버릇도 일부러 따라 함: after 로 끼워 넣으면 응답 results 에 '뒤쪽 기존 블록'까지 섞여 옴.
    (이걸 새 블록으로 착각하면 되돌리기가 멀쩡한 섹션을 지움 → blocks._append 가 막는지 테스트)
    """

    def __init__(self):
        self.ids = itertools.count(1)
        self.kids: dict[str, list[dict]] = {}  # 부모 id(페이지·블록) → 자식 블록 목록
        self.pages: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    # ---- 테스트에서 페이지 만들기
    def add_page(self, title: str, markdown: str) -> str:
        pid = f"page-{next(self.ids)}"
        self.pages[pid] = {"title": title, "props": {}}
        self.kids[pid] = []
        self._insert(pid, nblocks._markdown_to_blocks(markdown), after=None)
        return pid

    def texts(self, pid: str) -> list[str]:
        """페이지 최상위 블록을 '종류: 글' 목록으로 (비교하기 쉽게)."""
        return [f"{b['type']}: {nblocks._plain(b[b['type']].get('rich_text', []))}" for b in self.kids[pid]]

    # ---- 블록 저장 형태 (요청 형태 → GET 형태: id·has_children·plain_text 붙이기)
    def _store(self, req: dict) -> dict:
        t = req["type"]
        data = {k: v for k, v in req[t].items() if k != "children"}
        for key in ("rich_text", "caption"):
            if key in data:
                data[key] = [{**r, "plain_text": r.get("text", {}).get("content", "")} for r in data[key]]
        if t == "table_row":
            data["cells"] = [[{**r, "plain_text": r["text"]["content"]} for r in c] for c in data["cells"]]
        bid = f"blk-{next(self.ids)}"
        children = req[t].get("children") or []
        block = {"object": "block", "id": bid, "type": t, t: data, "has_children": bool(children)}
        self.kids[bid] = [self._store(c) for c in children]
        return block

    def _insert(self, parent: str, reqs: list[dict], after: str | None) -> list[dict]:
        lst = self.kids[parent]
        pos = len(lst) if after is None else next(i for i, b in enumerate(lst) if b["id"] == after) + 1
        new = [self._store(r) for r in reqs]
        lst[pos:pos] = new
        return lst[pos:] if after else new  # 진짜 Notion 버릇: after 면 뒤쪽 기존 블록까지 같이 돌려줌

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path))
        path = path.split("?")[0]
        parts = path.strip("/").split("/")
        if method == "GET" and parts[0] == "blocks" and parts[2] == "children":
            return {"results": [dict(b) for b in self.kids[parts[1]]], "has_more": False}
        if method == "PATCH" and parts[0] == "blocks" and parts[2] == "children":
            return {"results": self._insert(parts[1], body["children"], body.get("after"))}
        if method == "DELETE" and parts[0] == "blocks":
            for lst in self.kids.values():
                lst[:] = [b for b in lst if b["id"] != parts[1]]
            return {}
        if method == "POST" and path == "/pages":
            pid = f"page-{next(self.ids)}"
            title = nblocks._plain([{**r, "plain_text": r["text"]["content"]}
                                    for r in body["properties"][nclient.TITLE_PROP]["title"]])
            self.pages[pid] = {"title": title, "props": body["properties"]}
            self.kids[pid] = []
            self._insert(pid, body.get("children", []), None)
            return {"id": pid, "url": f"https://notion.so/{pid}"}
        if method == "PATCH" and parts[0] == "pages":
            self.pages.setdefault(parts[1], {}).update(body)
            return {"id": parts[1], "last_edited_time": datetime.now(timezone.utc).isoformat()}
        if method == "POST" and parts[0] == "data_sources":
            return {"results": [], "has_more": False}
        raise AssertionError(f"가짜 Notion 이 모르는 요청: {method} {path}")


@pytest.fixture
def notion(monkeypatch):
    fake = FakeNotion()
    for mod in _REQUEST_USERS:
        monkeypatch.setattr(mod, "_request", fake.request)
    monkeypatch.setattr(ntools, "_data_source_id", lambda: "ds-test")
    return fake


@pytest.fixture
def cache_with(isolate):
    """캐시 파일에 페이지 몇 개를 넣어 두는 도우미. 방금 동기화한 것처럼 synced_at 을 지금으로."""

    def make(pages: dict[str, dict]):
        full = {}
        for pid, p in pages.items():
            full[pid] = {"title": "", "type": "", "reviewed": False, "created": "2026-11-16",
                         "edited": "2026-11-16T00:00:00.000Z", "url": f"https://notion.so/{pid}", "text": "", **p}
        ncache.write_json(ncache.CACHE_FILE, {"synced_at": datetime.now(timezone.utc).isoformat(), "pages": full})
        return full

    return make

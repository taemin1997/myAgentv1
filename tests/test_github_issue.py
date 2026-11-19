"""GitHub 이슈: 모델은 초안만, 실제로 올리는 건 사람이 카드 버튼을 눌렀을 때만.
진짜 GitHub 에는 안 보냄 (_post 를 가짜로 바꿔서 무엇을 보냈는지만 봄)."""

import time

import tools.github as github
from tools import TOOLS_BY_NAME

REPO = "SKNETWORKS-FAMILY-AICAMP/SKN35-FINAL-4TEAM"
REAL_POST = github._post  # 파일을 읽을 때(conftest 가 막기 전) 원래 함수를 잡아 둠


def _no_network(*a, **k):
    raise AssertionError("실제 네트워크 요청")


def test_model_cannot_post_directly():
    # 모델이 부를 수 있는 건 초안 도구뿐. 실제로 올리는 create_issue 는 도구 목록에 없어야 함
    assert "draft_github_issue" in TOOLS_BY_NAME
    assert "create_issue" not in TOOLS_BY_NAME


def test_draft_without_screen_does_not_post():
    # 터미널·예전 화면: 올리기 버튼이 없으니 초안만 돌려주고 끝 (_post 는 conftest 에서 막혀 있어서 부르면 실패)
    out = github.draft_github_issue("로그인 버그", "## 재현 방법\n1. 누름", repo=REPO)
    assert "올리지 못함" in out and "로그인 버그" in out


def test_draft_goes_to_handler_with_resolved_repo(monkeypatch):
    got = []
    monkeypatch.setattr(github, "ISSUE_DRAFT_HANDLER", got.append)
    out = github.draft_github_issue("제목", "본문", labels="bug,  frontend ,", repo=REPO)
    assert "아직 안 올라갔음" in out
    assert got == [{"repo": REPO, "title": "제목", "body": "본문", "labels": "bug, frontend", "has_token": False}]
    assert "GITHUB_TOKEN" in out  # 토큰이 없으면 모델이 안내하게


def test_real_post_refuses_without_token(monkeypatch):
    # 진짜 _post: 토큰이 없으면 요청을 아예 안 보내고 안내만 (urlopen 은 막아서 혹시 나가도 실패)
    monkeypatch.setattr(github.urllib.request, "urlopen", _no_network)
    try:
        REAL_POST("/repos/o/r/issues", {"title": "x"})
    except github.GitHubError as e:
        assert "GITHUB_TOKEN" in str(e)
    else:
        raise AssertionError("토큰 없이 올라가면 안 됨")


def test_create_issue_payload(monkeypatch):
    sent = []

    def fake_post(path, payload):
        sent.append((path, payload))
        return {"number": 7, "html_url": "https://github.com/o/r/issues/7"}

    monkeypatch.setattr(github, "_post", fake_post)
    r = github.create_issue(" 제목 ", "본문\n", "bug, ui", REPO)
    assert r == {"number": 7, "url": "https://github.com/o/r/issues/7"}
    assert sent == [(f"/repos/{REPO}/issues", {"title": "제목", "body": "본문", "labels": ["bug", "ui"]})]


def test_web_card_posts_once(monkeypatch):
    import ui.web_app as W

    sent = []

    def fake_post(path, payload):
        time.sleep(0.05)  # 올리는 동안 한 번 더 눌러도 두 번 안 올라가는지 보려고
        sent.append(payload["title"])
        return {"number": 3, "html_url": "https://github.com/o/r/issues/3"}

    monkeypatch.setattr(github, "_post", fake_post)
    api = W.Api()
    # 도구가 돌면 Api 가 받은 초안이 화면 이벤트로 감
    github.draft_github_issue("버그", "본문", repo=REPO)
    ev = [e for e in api.poll()["events"] if e["type"] == "issue_draft"][0]
    assert ev["repo"] == REPO and ev["title"] == "버그"

    assert api.post_issue(ev["id"], "버그(수정)", "본문", "")["ok"]
    assert api.post_issue(ev["id"], "버그(수정)", "본문", "")["ok"] is False  # 두 번 눌러도 하나만
    deadline, events = time.time() + 3, []
    while time.time() < deadline and not any(e["type"] == "issue_posted" for e in events):
        time.sleep(0.02)
        events += api.poll()["events"]
    assert sent == ["버그(수정)"]  # 카드에서 고친 제목으로 올라감
    assert api.post_issue(ev["id"], "또", "", "")["ok"] is False  # 올린 카드는 다시 못 올림
    assert api.post_issue(999, "없는 카드", "", "")["ok"] is False

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


# ------------------------------------------------------------ 닫기·삭제
def _issue(author="taemin1997", pr=False):
    i = {"number": 2, "title": "테스트", "state": "open", "user": {"login": author}, "comments": 0,
         "html_url": "https://github.com/o/r/issues/2", "node_id": "I_node2"}
    if pr:
        i["pull_request"] = {}
    return i


def test_model_cannot_close_or_delete_directly():
    assert "draft_issue_removal" in TOOLS_BY_NAME
    assert not {"close_issue", "delete_issue"} & set(TOOLS_BY_NAME)


def test_removal_card_shows_issue(monkeypatch):
    got = []
    monkeypatch.setattr(github, "ISSUE_REMOVAL_HANDLER", got.append)
    monkeypatch.setattr(github, "_get", lambda path: _issue())
    out = github.draft_issue_removal(number=2, repo=REPO)
    assert "아직 아무것도 안 바뀜" in out
    assert got[0]["number"] == 2 and got[0]["mine"] is True and got[0]["node_id"] == "I_node2"


def test_removal_refuses_pr(monkeypatch):
    monkeypatch.setattr(github, "_get", lambda path: _issue(pr=True))
    assert "PR" in github.draft_issue_removal(number=2, repo=REPO)


def test_delete_only_my_issue(monkeypatch):
    sent = []
    monkeypatch.setattr(github, "_post", lambda path, payload, method="POST": sent.append(path) or {})
    monkeypatch.setattr(github, "_get", lambda path: _issue(author="teammate"))
    try:
        github.delete_issue(REPO, 2)
    except github.GitHubError as e:
        assert "teammate" in str(e)
    else:
        raise AssertionError("팀원 이슈를 지우면 안 됨")
    assert sent == []  # 삭제 요청 자체를 안 보냄


def test_delete_and_close_requests(monkeypatch):
    sent = []

    def fake_post(path, payload, method="POST"):
        sent.append((method, path, payload))
        return {"data": {"deleteIssue": {}}} if path == "/graphql" else {"state": "closed"}

    monkeypatch.setattr(github, "_post", fake_post)
    monkeypatch.setattr(github, "_get", lambda path: _issue())
    github.delete_issue(REPO, 2)
    github.close_issue(REPO, 2)
    assert sent[0][1] == "/graphql" and sent[0][2]["variables"] == {"id": "I_node2"}
    assert sent[1] == ("PATCH", f"/repos/{REPO}/issues/2", {"state": "closed", "state_reason": "not_planned"})


def test_delete_graphql_permission_error(monkeypatch):
    monkeypatch.setattr(github, "_get", lambda path: _issue())
    monkeypatch.setattr(github, "_post", lambda *a, **k: {"errors": [{"type": "FORBIDDEN", "message": "no permission"}]})
    try:
        github.delete_issue(REPO, 2)
    except github.GitHubError as e:
        assert "권한" in str(e)
    else:
        raise AssertionError("GraphQL errors 를 성공으로 보면 안 됨")


def test_web_removal_card_runs_once(monkeypatch):
    import ui.web_app as W

    calls = []
    monkeypatch.setattr(github, "_get", lambda path: _issue())
    monkeypatch.setattr(github, "_post", lambda path, payload, method="POST": (time.sleep(0.05), calls.append(path))[1] or {})
    api = W.Api()
    github.draft_issue_removal(number=2, repo=REPO)
    ev = [e for e in api.poll()["events"] if e["type"] == "issue_removal"][0]
    assert "node_id" not in ev  # 화면엔 안 보냄
    assert api.remove_issue(ev["id"], "wipe")["ok"] is False  # 모르는 동작
    assert api.remove_issue(ev["id"], "delete")["ok"]
    assert api.remove_issue(ev["id"], "delete")["ok"] is False  # 두 번 안 지움
    deadline, events = time.time() + 3, []
    while time.time() < deadline and not any(e["type"] == "issue_removed" for e in events):
        time.sleep(0.02)
        events += api.poll()["events"]
    assert calls == ["/graphql"]
    assert api.remove_issue(ev["id"], "close")["ok"] is False  # 지운 카드는 끝


def test_removal_by_title(monkeypatch):
    # 번호 대신 제목 일부로 찾기. PR(#1)은 목록에 섞여 와도 빼야 함
    rows = [{"number": 1, "title": "Git 환경설정", "state": "closed", "pull_request": {}, "user": {"login": "taemin1997"}},
            {"number": 2, "title": "Git issue 테스트", "state": "open", "user": {"login": "taemin1997"}},
            {"number": 5, "title": "로그인 버그", "state": "open", "user": {"login": "taemin1997"}}]
    paths = []

    def fake_get(path):
        paths.append(path)
        return rows if "/issues?" in path else _issue()

    got = []
    monkeypatch.setattr(github, "_get", fake_get)
    monkeypatch.setattr(github, "ISSUE_REMOVAL_HANDLER", got.append)

    assert "확인 카드" in github.draft_issue_removal("issue 테스트", repo=REPO)  # 공백·대소문자 무시
    assert paths[-1] == f"/repos/{REPO}/issues/2" and len(got) == 1
    many = github.draft_issue_removal("git", repo=REPO)  # PR은 빠져서 1개 → 카드
    assert "확인 카드" in many and len(got) == 2
    listing = github.draft_issue_removal(repo=REPO)  # 제목 없이 → 목록만, 카드 안 띄움
    assert "#2" in listing and "#5" in listing and "#1 " not in listing and len(got) == 2
    assert "없음" in github.draft_issue_removal("없는 제목", repo=REPO)

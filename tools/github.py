"""GitHub 도구: 팀 저장소에서 '내 커밋'을 날짜별로 가져옴.

[왜 필요한가]
최종 프로젝트 페이지의 '내가 한 일'은 회의록에 잘 안 나옴 (회의록은 팀 결정 위주).
실제로 내가 한 일의 근거는 커밋이라서, 갱신할 때 그날 내 커밋을 읽어서 채움.
→ 포트폴리오·면접 때 "이 기능은 내가 이 커밋으로 만들었음" 근거가 매일 쌓임.

[왜 모든 브랜치를 보나]
팀이 개인 브랜치(taemin, minha …)에서 작업하고 나중에 dev/main으로 합침.
기본 브랜치만 보면 아직 안 합친 내 작업이 안 보여서, 브랜치마다 내 커밋을 찾고 같은 커밋(sha)은 하나로 합침.

[요청 한도]
토큰 없이 공개 저장소를 읽으면 시간당 60번까지. 하루 갱신 한 번에 10~20번 정도 씀.
더 필요하면 .env에 GITHUB_TOKEN 넣으면 시간당 5000번.

[이슈 올리기]
이슈는 팀원 모두가 보고 알림도 가서, 모델이 혼자 올리면 안 됨. 그래서 두 단계로 나눔:
  1) 모델은 draft_github_issue 로 '초안'만 만듦 → 화면에 이슈 카드가 뜸
  2) 사용자가 카드에서 제목·본문을 확인(수정)하고 'GitHub에 올리기'를 눌러야 create_issue 가 불림
create_issue 는 도구 목록(ALL_TOOLS)에 없어서 모델이 직접 부를 방법이 없음.
쓰기는 인증이 꼭 필요함 → GITHUB_TOKEN 에 이슈 쓰기 권한(classic 토큰이면 public_repo) 필요.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

from core.toolkit import tool

API = "https://api.github.com"
DEFAULT_REPO = "SKNETWORKS-FAMILY-AICAMP/SKN35-FINAL-4TEAM"  # 최종 프로젝트 저장소 (공개)
DEFAULT_AUTHOR = "taemin1997"
KST = timezone(timedelta(hours=9))
MAX_DETAIL = 12  # 파일 목록까지 볼 커밋 수 (커밋마다 요청 1번씩 들어서 제한)


class GitHubError(Exception):
    pass


def _get(path: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "my-agent"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubError("GitHub 요청 한도(시간당 60번)를 다 씀. 한 시간 뒤에 다시 하거나 .env에 GITHUB_TOKEN을 넣으면 됨")
        if e.code == 404:
            raise GitHubError("저장소를 못 찾음 (이름이 틀렸거나 비공개 저장소면 GITHUB_TOKEN 필요)")
        raise GitHubError(f"GitHub 오류 {e.code}")
    except urllib.error.URLError as e:
        raise GitHubError(f"GitHub 연결 실패: {e.reason}")


def _post(path: str, payload: dict):
    """쓰기 요청. 읽기(_get)와 달리 토큰이 없으면 아예 안 보냄 (어차피 401이라 요청 한도만 날림)."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise GitHubError("이슈를 올리려면 .env에 GITHUB_TOKEN이 필요함 (classic 토큰이면 public_repo 권한)")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "my-agent",
               "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # 오류 이유를 사람이 알아들을 말로 (토큰 값은 절대 메시지에 안 넣음)
        if e.code == 401:
            raise GitHubError("GitHub 토큰이 틀렸거나 만료됨 (.env의 GITHUB_TOKEN 확인)")
        if e.code in (403, 404):
            raise GitHubError("이 저장소에 이슈를 쓸 권한이 없음. 토큰 권한(이슈 쓰기/public_repo)과 "
                              "팀 저장소 협업자인지 확인 (조직 저장소는 fine-grained 토큰이면 조직 승인이 필요할 수 있음)")
        if e.code == 410:
            raise GitHubError("이 저장소는 이슈 기능이 꺼져 있음")
        if e.code == 422:
            raise GitHubError("GitHub가 내용을 거절함 (제목이 비었거나 라벨 형식이 이상함)")
        raise GitHubError(f"GitHub 오류 {e.code}")
    except urllib.error.URLError as e:
        raise GitHubError(f"GitHub 연결 실패: {e.reason}")


def _day_range(date: str, days: int) -> tuple[str, str]:
    """한국 날짜 기준 [date - (days-1) 0시, date 24시) → GitHub가 받는 UTC ISO 시각."""
    end_day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
    start_day = end_day - timedelta(days=max(days, 1))
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return fmt(start_day), fmt(end_day)


def resolve_repo(repo: str, author: str) -> str:
    """'myAgentv1'처럼 이름만 오면 'owner/name'으로 바꿈.
    모델이 소유자를 빼고 이름만 넘기는 경우가 있어서 (실제로 그랬음):
      1) 내 저장소 목록에서 같은 이름(대소문자 무시) 찾기
      2) 없으면 최종 프로젝트 조직(SKNETWORKS-FAMILY-AICAMP)의 저장소로 보고 확인
    URL(https://github.com/owner/name)로 와도 owner/name만 뽑음."""
    repo = repo.strip().removesuffix(".git").rstrip("/")
    if "github.com/" in repo:
        repo = "/".join(repo.split("github.com/", 1)[1].split("/")[:2])
    if "/" in repo:
        return repo
    for r in _get(f"/users/{author}/repos?per_page=100&sort=pushed"):
        if r["name"].lower() == repo.lower():
            if r.get("fork"):
                # 팀 저장소를 포크해 둔 경우: 실제 작업(브랜치·커밋)은 원본에 있어서 원본을 씀
                return _get(f"/repos/{r['full_name']}").get("parent", {}).get("full_name", r["full_name"])
            return r["full_name"]
    org = DEFAULT_REPO.split("/")[0]
    try:
        return _get(f"/repos/{org}/{urllib.parse.quote(repo)}")["full_name"]
    except GitHubError:
        raise GitHubError(f"'{repo}' 저장소를 못 찾음 ({author}의 공개 저장소와 {org} 조직에서 찾아봄). "
                          "이름을 확인하거나 'owner/이름' 형식으로 알려줘")


def my_commits(date: str = "", days: int = 1, repo: str = "", author: str = "") -> list[dict]:
    """모든 브랜치에서 author의 커밋을 모아 날짜순으로. 머지 커밋은 뺌.
    반환: [{"sha","time"(KST),"branches","message","files":[(파일, +추가, -삭제)], "url"}]"""
    author = author or os.getenv("GITHUB_AUTHOR", DEFAULT_AUTHOR)
    repo = resolve_repo(repo or os.getenv("GITHUB_REPO", DEFAULT_REPO), author)
    date = date or datetime.now(KST).strftime("%Y-%m-%d")
    since, until = _day_range(date, days)

    found: dict[str, dict] = {}
    for b in _get(f"/repos/{repo}/branches?per_page=100"):
        q = f"/repos/{repo}/commits?sha={b['name']}&author={author}&since={since}&until={until}&per_page=100"
        for c in _get(q):
            msg = c["commit"]["message"]
            if msg.startswith("Merge "):  # 머지 커밋은 내가 한 작업 내용이 아니라서 제외
                continue
            item = found.setdefault(c["sha"], {
                "sha": c["sha"][:7], "full_sha": c["sha"], "branches": [], "message": msg.strip(),
                "time": datetime.fromisoformat(c["commit"]["author"]["date"].replace("Z", "+00:00"))
                        .astimezone(KST).strftime("%m/%d %H:%M"),
                "url": c["html_url"], "files": []})
            item["branches"].append(b["name"])

    commits = sorted(found.values(), key=lambda c: c["time"])
    for c in commits[:MAX_DETAIL]:  # 무슨 파일을 얼마나 고쳤는지 (커밋 메시지가 짧을 때 근거가 됨)
        detail = _get(f"/repos/{repo}/commits/{c['full_sha']}")
        c["files"] = [(f["filename"], f.get("additions", 0), f.get("deletions", 0)) for f in detail.get("files", [])]
    return commits


@tool
def get_my_commits(date: str = "", days: int = 1, repo: str = "", author: str = "") -> str:
    """팀 GitHub 저장소에서 사용자(taemin1997)가 그날 한 커밋을 모든 브랜치에서 가져옵니다.
    최종 프로젝트 갱신 때 '내가 한 일'·진행 기록을 채우는 근거로 쓰세요. 커밋 메시지와 바뀐 파일을 보고
    무슨 작업인지 한 줄로 요약하되, 커밋에 없는 내용은 지어내지 마세요. 커밋이 없으면 없다고 하세요.

    Args:
        date: 기준 날짜 YYYY-MM-DD (한국 시간). 빈 문자열이면 오늘.
        days: 며칠치를 볼지. 1이면 그날 하루, 3이면 그날 포함 최근 3일 (주말 작업까지 볼 때).
        repo: 저장소. 'myAgentv1'처럼 이름만 써도 됨 ('owner/name'이나 GitHub 주소도 됨). 빈 문자열이면 최종 프로젝트 저장소.
        author: GitHub 아이디. 빈 문자열이면 taemin1997.
    """
    author = author or os.getenv("GITHUB_AUTHOR", DEFAULT_AUTHOR)
    if date:  # 날짜 오류를 다른 오류(예: 한글 저장소 이름)와 헷갈리지 않게 먼저 따로 확인
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return f"날짜 형식이 이상함: {date} (YYYY-MM-DD)"
    try:
        # 저장소 이름을 먼저 owner/name으로 확정 (요청 한도 아끼려고 한 번만)
        where = resolve_repo(repo or os.getenv("GITHUB_REPO", DEFAULT_REPO), author)
        commits = my_commits(date, days, where, author)
    except GitHubError as e:
        return str(e)
    label = f"{where} · {date or '오늘'}" + (f" 포함 최근 {days}일" if days > 1 else "")
    if not commits:
        return (f"{label} 내 커밋 없음 (모든 브랜치 확인함). "
                "로컬에서만 커밋하고 push 안 한 건 안 보임. 다른 저장소 작업이면 repo를 지정해서 다시 볼 것.")
    lines = [f"{label} 내 커밋 {len(commits)}개 (모든 브랜치, 머지 커밋 제외):"]
    for c in commits:
        lines.append(f"- {c['time']} [{', '.join(c['branches'])}] {c['message'].splitlines()[0]}  ({c['sha']}, {c['url']})")
        body = c["message"].splitlines()[1:]
        if any(l.strip() for l in body):
            lines.append("  본문: " + " / ".join(l.strip() for l in body if l.strip())[:300])
        if c["files"]:
            shown = ", ".join(f"{f} (+{a}/-{d})" for f, a, d in c["files"][:8])
            more = f" 외 {len(c['files']) - 8}개" if len(c["files"]) > 8 else ""
            lines.append(f"  파일: {shown}{more}")
    if len(commits) > MAX_DETAIL:
        lines.append(f"(파일 목록은 처음 {MAX_DETAIL}개 커밋만)")
    return "\n".join(lines)


# ------------------------------------------------------------ 이슈
# 화면이 '이슈 초안이 생겼다'는 걸 받는 자리. 웹 화면(ui/web_app.Api)이 켜질 때 자기 함수를 넣음.
# 비어 있으면(터미널·예전 화면) 올리기 버튼이 없어서 초안만 보여주고 끝냄.
ISSUE_DRAFT_HANDLER: Callable[[dict], None] | None = None


def _split_labels(labels: str) -> list[str]:
    return [l.strip() for l in (labels or "").replace("，", ",").split(",") if l.strip()]


def create_issue(title: str, body: str = "", labels: str = "", repo: str = "") -> dict:
    """실제로 이슈를 올림. 사용자가 카드의 'GitHub에 올리기'를 눌렀을 때만 불림 (모델 도구 아님).
    반환: {"number", "url"}"""
    title = (title or "").strip()
    if not title:
        raise GitHubError("제목이 비어 있음")
    repo = repo or os.getenv("GITHUB_REPO", DEFAULT_REPO)
    if repo.count("/") != 1:  # 카드에는 draft 때 확정한 owner/name이 오지만, 혹시 몰라서
        repo = resolve_repo(repo, os.getenv("GITHUB_AUTHOR", DEFAULT_AUTHOR))
    payload = {"title": title, "body": (body or "").strip()}
    if label_list := _split_labels(labels):
        payload["labels"] = label_list
    r = _post(f"/repos/{repo}/issues", payload)
    return {"number": r.get("number"), "url": r.get("html_url", "")}


@tool
def draft_github_issue(title: str, body: str, labels: str = "", repo: str = "") -> str:
    """팀 GitHub 저장소에 올릴 이슈 '초안'을 만들어 화면에 카드로 띄웁니다. 이 도구는 이슈를 올리지 않습니다.
    사용자가 카드에서 내용을 확인하고 'GitHub에 올리기' 버튼을 눌러야 실제로 올라갑니다.
    사용자가 이슈를 작성·등록·올려 달라고 할 때 씁니다. 이미 올렸다고 말하지 마세요.
    본문은 마크다운으로, 사용자가 말한 내용과 대화·자료에 있는 사실만 쓰세요 (모르는 칸은 비우거나 '(직접 작성)').
    보통 '## 배경', '## 할 일'(- [ ] 체크리스트), '## 완료 기준' 순서. 버그면 '## 재현 방법', '## 기대 동작', '## 실제 동작'.
    제목은 한 줄로 무엇을 할지/무엇이 문제인지. 라벨은 사용자가 말했을 때만.

    Args:
        title: 이슈 제목 (한 줄)
        body: 이슈 본문 (마크다운)
        labels: 라벨, 쉼표로 구분 (예: 'bug, frontend'). 없으면 빈 문자열.
        repo: 저장소. 빈 문자열이면 최종 프로젝트 팀 저장소. 이름만 써도 됨.
    """
    title = (title or "").strip()
    if not title:
        return "제목이 비어 있음. 제목을 정해서 다시 부를 것"
    try:
        # 저장소는 초안 때 확정해 둠 → 올리기 버튼에서 다시 찾느라 요청 한도를 안 씀
        where = resolve_repo(repo or os.getenv("GITHUB_REPO", DEFAULT_REPO),
                             os.getenv("GITHUB_AUTHOR", DEFAULT_AUTHOR))
    except GitHubError as e:
        return str(e)
    draft = {"repo": where, "title": title, "body": (body or "").strip(), "labels": ", ".join(_split_labels(labels)),
             "has_token": bool(os.getenv("GITHUB_TOKEN"))}
    if ISSUE_DRAFT_HANDLER is None:
        return (f"이슈 초안 ({where}) — 이 화면에는 올리기 버튼이 없어서 올리지 못함. "
                "웹 화면(uv run app.py)에서 다시 요청하거나 GitHub에서 직접 붙여 넣을 것.\n"
                f"제목: {title}\n{draft['body']}")
    ISSUE_DRAFT_HANDLER(draft)
    note = "" if draft["has_token"] else " 단, .env에 GITHUB_TOKEN이 없어서 지금은 올리기가 실패함 (토큰 설정 안내할 것)."
    return (f"{where}에 올릴 이슈 초안을 화면에 카드로 띄움. 아직 안 올라갔음. "
            "사용자에게 카드에서 확인·수정 후 'GitHub에 올리기'를 누르라고 짧게 안내하고, 본문을 다시 붙여넣지 말 것." + note)


GITHUB_TOOLS = [get_my_commits, draft_github_issue]

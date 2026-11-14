"""노트 정리 프롬프트(템플릿) 저장소.

왜 파일로 따로 두나:
- 강의 노트, 회의록, 주간 회고는 정리 형식이 다 다름. 형식을 코드 안에 박아 두면
  바꿀 때마다 exe를 다시 빌드해야 함.
- 그래서 prompts 폴더에 템플릿 하나당 .md 파일 하나로 둠. 메모장으로 고쳐도 되고,
  채팅에서 "회의록은 이렇게 정리해줘, 기억해"라고 하면 save_note_template 도구가 파일을 고침.
- 대화 전체를 저장하는 게 아니라 '정리하는 방법'만 저장하니까 토큰이 거의 안 늘어남.
  (시스템 프롬프트에는 템플릿 이름만 넣고, 본문은 정리할 때만 읽음)

파일 형식 (prompts/회의록.md):
    설명: 최종 프로젝트 회의록·자료 → 그날 일차 노트
    분류: 일차 노트          ← Notion '분류' 속성 (일차 노트/회고/프로젝트/면접/기타)
    유형: 실습               ← Notion '유형' 속성 (강의/실습, 비우면 안 넣음)
    과목: (비움)             ← Notion '과목' 속성. N일차는 커리큘럼에서 자동, 프로젝트는 '최종 프로젝트' 등
    제목: N일차              ← 제목 규칙 (N일차 / N주차 회고 / 자유)
    ---
    (여기부터 모델에게 줄 정리 지시문)
"""

from dataclasses import dataclass

from paths import APP_DIR

PROMPT_DIR = APP_DIR / "prompts"
CATEGORIES = ("일차 노트", "회고", "프로젝트", "면접", "기타")  # Notion DB '분류' 선택지와 같아야 함
# Notion DB 탭과 같은 아이콘·순서. 드롭다운도 탭 이름으로 보여줘서 "어느 탭에 들어갈지" 바로 알게 함
TAB_ICON = {"일차 노트": "📅", "회고": "📝", "프로젝트": "🚀", "면접": "🎯", "기타": "📄"}

# 공통 말투 규칙. 모든 템플릿 앞에 붙음 (템플릿마다 반복해서 쓰지 않으려고)
COMMON_RULES = """마크다운으로 작성, 반말체 ~임/~함 (명령조 어미 X), 제목은 ###까지만.
자료에 없는 내용을 지어내지 말 것. 보충 설명을 덧붙일 땐 '(보충)'이라고 표시.
자료끼리 내용이 다르거나 날짜·결정이 불확실하면 '확인 필요'라고 적을 것."""

# 처음 실행할 때 prompts 폴더가 비어 있으면 이 기본 템플릿들을 파일로 만들어 줌.
# 기존 Notion 노트(80~88일차, N주차 회고)를 정리했던 형식 그대로임.
DEFAULTS = {
    "강의노트": ("강의 PDF → 그날 일차 노트", "일차 노트", "강의", "N일차", """\
부트캠프 강의 자료를 복습용 노트로 정리함. 구조:
# 주제 한 줄 요약
## 오늘 배운 것 (핵심 키워드 3~6개 bullet)
## 주제별 정리 → 각 주제마다 "무엇인지 → 왜 필요한지 → 어떻게 동작하는지" 순서
## 코드 (자료에 코드가 있으면 핵심만, 주석으로 이유 설명)
## 헷갈리기 쉬운 점 / 비교 (있으면)
## 한 줄 복습
슬라이드 문장을 그대로 베끼지 말고 이해하기 쉽게 재구성하되, 주요 주제는 빠뜨리지 말 것."""),

    "회의록": ("프로젝트 회의록·기획 자료 → 그날 일차 노트", "일차 노트", "실습", "N일차", """\
팀 프로젝트 회의록·기획 자료를 그날 한 일 노트로 정리함. ('📋 최종 프로젝트 일일 노트 템플릿' 페이지와 같은 구조)
# 최종 프로젝트 K일차: 그날 핵심 주제 (K = 일차 번호 - 82, 예: 89일차 → 7일차)
> 오늘의 한 줄 목표: (자료에서 드러나는 그날 목표)
- 자료: 무슨 자료를 보고 정리했는지 (파일 종류, 자료에 적힌 날짜). 그날 마감·일정이 있으면 한 줄씩
---
## ✅ 오늘 한 일
- **팀**: 회의·결정·팀 전체 작업
- **내 작업**: 자료에 내 이름(고태민)·담당이 나오면 그것만. 없으면 '(직접 작성)'
## 💬 주요 논의 (안건마다 ### 하나, 각 안건은 배경 → 나온 의견 → 결론)
## 📌 결정 사항 vs 미확정
| 구분 | 내용 | 담당 | 비고 |
(✅ 확정 / ⏳ 미확정 행으로. 미확정은 비고에 '다음 회의 안건')
## 🚧 막힌 점 · 해결 (문제 → 원인 → 해결/시도. 없으면 '없음')
## 🗓️ 다음 작업
- [ ] 할 일 — 담당 / 기한
## 📚 개념 정리 (보충) (처음 나온 개념만: 한 줄 정의, 왜 필요한지, 우리 프로젝트에서 어디에 쓰이는지)
## 🎯 면접 연결 (오늘 한 일 중 면접에서 말할 만한 것: 예상 질문 → 답변 포인트)
---
> **한 줄 복습** — 오늘을 한 문장으로
회의 내용을 시간순으로 받아쓰지 말고 주제별로 묶을 것. 결정 사항은 **굵게**.
자료에 없는 칸은 지우지 말고 '없음' 또는 '(직접 작성)'으로 남김."""),

    "최종갱신": ("하루 자료로 최종 프로젝트 페이지 갱신 + 그날 일차 노트 (채팅에서 사용)", "프로젝트", "", "갱신", """\
이 템플릿은 정리 형식이 아니라 '매일 하는 작업 순서'임. 최종 프로젝트 페이지 제목: 최종 프로젝트 — Compare Agent 2.1
1. 그날 일차 노트: get_note_template('회의록') 형식으로 N일차 노트 정리 → 보여주고 확인 → save_study_note
2. 최종 프로젝트 페이지 갱신:
   a. read_study_note('최종 프로젝트 — Compare Agent 2.1')로 현재 내용을 읽음
   b. 오늘 자료로 바뀌는 섹션만 고름. 섹션별 방식:
      - replace (최신 상태로 교체): 개요, 팀 구성, 기술 스택, 시스템 구조, 결과 / 평가 지표
      - append (날마다 쌓기): 내가 한 일, 트러블슈팅, 배운 점, 면접 예상 질문, 진행 기록
      - replace 할 때는 기존 내용 중 여전히 맞는 건 살리고 바뀐 부분만 고친 '섹션 전체'를 넘김 (기존 내용 날리지 말 것)
      - append 할 때는 오늘 새로 생긴 줄만 넘김 (기존 줄을 다시 넣으면 중복됨)
      - '(작성 예정)', '(직접 작성)' 자리표시는 실제 내용이 생기면 replace로 바꿔서 없앰. 단 '(직접 작성)'은
        자료에 본인 내용이 없으면 그대로 둠
      - 표는 '| 영역 | 기술 |' 처럼 줄 양끝에 | 를 붙여서 씀
      - 자료끼리 충돌하면 최신 자료 기준으로 쓰고 '(11/16 변경)'처럼 바뀐 날짜를 표시
   c. 진행 기록에는 항상 한 줄 추가: '- 11/16 (89일차): 그날 핵심 한 줄 · [89일차 노트](노트 url)'
   d. 바꿀 섹션 목록과 섹션별 바뀌는 내용 요약을 보여주고 확인받은 뒤 update_page_section으로 반영
3. 끝나면 무엇이 바뀌었는지 한눈에 보이게 짧게 보고.
최종 프로젝트 페이지가 없으면 get_note_template('프로젝트') 형식으로 먼저 만들지 사용자에게 물어볼 것."""),

    "프로젝트": ("프로젝트 소개 페이지 (미니프로젝트 1~4 형식). 자료 없으면 빈 틀", "프로젝트", "", "자유", """\
프로젝트 하나를 포트폴리오용 소개 페이지로 정리함. 미니프로젝트 1~4 페이지와 같은 구조:
> 한 줄 소개 (무엇을 만드는 프로젝트인지)
- 기간 · 팀 · 본인 역할 · 과제 주제 · GitHub 저장소 링크
---
## 1. 개요 (문제 → 해결 방법, 핵심 기능)
## 2. 팀 구성 (이름 | 담당 영역 표)
## 3. 내가 한 일 (왜 그렇게 했는지까지)
## 4. 기술 스택 (영역 | 기술 표)
## 5. 시스템 구조 (흐름을 단계별로)
## 6. 결과 / 평가 지표
## 7. 트러블슈팅 (문제 → 원인 → 해결)
## 8. 배운 점
## 9. 진행 기록 (진행 중인 프로젝트만. '- 11/16 (89일차): 핵심 한 줄 · [노트 링크]' 한 줄씩 쌓음)
## 면접 예상 질문 (Q1~Q5, 본인 역할 질문 포함. 답은 자료 근거로 짧게)
자료가 없거나 부족한 칸은 지어내지 말고 '(작성 예정)'으로 남겨 둠.
사용자가 '빈 템플릿', '틀만'이라고 하면 위 섹션 제목과 '(작성 예정)'만 있는 빈 틀로 만듦.
최종 프로젝트면 과목은 '최종 프로젝트', 단위 프로젝트면 '단위 프로젝트'.""", "최종 프로젝트"),

    "면접": ("자료 → 면접 예상 질문·답변 (면접 예상 페이지 형식)", "면접", "", "자유", """\
자료(노트·프로젝트 문서)를 보고 면접 예상 질문과 답변을 정리함. '면접 예상' 페이지와 같은 형식:
## 주제 (예: 최종 프로젝트 — Compare Agent, RAG, 통계 …)
Q1. 질문
> 답변: 2~4문장. 결론 먼저 → 이유 → 우리 프로젝트에서의 예시 순서
(주제마다 Q 3~6개, 번호는 이어서)
질문 고르는 기준:
- 본인 역할 질문을 꼭 포함 ('왜 그렇게 했나', '다시 하면 뭘 바꾸나')
- 개념 정의만 묻는 질문보다 '왜 / 언제 / 비교' 질문 위주
- 답변은 자료 근거로만. 본인 경험·수치가 자료에 없으면 답변 칸에 '(직접 작성)'
기존 '면접 예상' 페이지에 덧붙이는 거면 새 페이지를 만들지 말고
update_page_section(title='면접 예상', section=주제, mode='append')로 이어 쓸 것."""),

    "주간회고": ("한 주 정리 → N주차 회고", "회고", "", "N주차 회고", """\
한 주 동안의 노트·자료를 보고 주간 회고 초안을 씀. 아래 다섯 칸을 ## 제목으로:
## 일주일 동안 한 일에서 느낀 점
## 좋았거나 내가 잘한 점
## 아쉬웠던 점
## 개선할 점
## 다음주 계획
각 칸은 bullet 2~3개. 한 일(사실)은 자료를 근거로 채우고,
느낀 점·감정처럼 본인만 아는 칸은 지어내지 말고 '(직접 작성)'이라고 비워 둘 것."""),
}


@dataclass
class Template:
    name: str
    description: str
    category: str   # Notion 분류
    note_type: str  # Notion 유형 (강의/실습/빈칸)
    title_rule: str  # N일차 / N주차 회고 / 자유
    body: str
    topic: str = ""  # Notion 과목. 'N일차'는 커리큘럼에서 자동이라 비워 둠 (프로젝트 페이지용)

    def system_prompt(self) -> str:
        return f"{COMMON_RULES}\n\n{self.body}"


def _path(name: str):
    return PROMPT_DIR / f"{name}.md"


def _write(t: Template) -> None:
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    head = (f"설명: {t.description}\n분류: {t.category}\n유형: {t.note_type}\n과목: {t.topic}\n"
            f"제목: {t.title_rule}\n---\n")
    _path(t.name).write_text(head + t.body.strip() + "\n", encoding="utf-8")


def _parse(name: str, text: str) -> Template:
    head, sep, body = text.partition("\n---\n")
    if not sep:  # 머리말 없이 본문만 쓴 파일도 받아 줌
        head, body = "", text
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    category = meta.get("분류", "일차 노트")
    return Template(name=name, description=meta.get("설명", ""),
                    category=category if category in CATEGORIES else "기타",
                    note_type=meta.get("유형", ""), title_rule=meta.get("제목", "N일차"),
                    body=body.strip(), topic=meta.get("과목", ""))


def ensure_defaults() -> None:
    """기본 템플릿 파일이 없으면 만든다. 사용자가 고친 파일은 덮어쓰지 않음."""
    for name, (desc, cat, typ, rule, body, *topic) in DEFAULTS.items():
        if not _path(name).exists():
            _write(Template(name, desc, cat, typ, rule, body, topic[0] if topic else ""))


def load_templates() -> dict[str, Template]:
    ensure_defaults()
    return {p.stem: _parse(p.stem, p.read_text(encoding="utf-8")) for p in sorted(PROMPT_DIR.glob("*.md"))}


def card_template_labels() -> dict[str, str]:
    """정리 카드 드롭다운: {보여줄 이름: 템플릿 이름}. Notion 탭 순서·아이콘에 맞춤.
      📅 일차 노트 · 강의 / 📅 일차 노트 · 회의록 / 📝 회고 / 🚀 프로젝트 / 🎯 면접
    한 탭에 템플릿이 여러 개면 '· 이름'을 붙여 구분함 (강의노트 → '강의').
    '갱신' 템플릿(최종갱신)은 기존 페이지를 읽고 고치는 작업이라 도구를 쓸 수 있는 채팅에서만 됨 → 뺌."""
    usable = [t for t in load_templates().values() if t.title_rule != "갱신"]
    usable.sort(key=lambda t: (list(TAB_ICON).index(t.category), t.name))
    labels = {}
    for t in usable:
        same_tab = sum(1 for x in usable if x.category == t.category)
        label = f"{TAB_ICON[t.category]} {t.category}"
        if same_tab > 1:
            label += " · " + (t.name.removesuffix("노트") or t.name)
        labels[label] = t.name
    return labels or {"📅 일차 노트": "강의노트"}


def get_template(name: str) -> Template | None:
    return load_templates().get(name.strip())


def save_template(name: str, body: str, description: str = "", category: str = "",
                  note_type: str = "", title_rule: str = "", topic: str = "") -> Template:
    """새 템플릿을 만들거나 기존 템플릿을 고친다. 빈 값은 기존 값을 유지."""
    name = name.strip().replace("/", "_").replace("\\", "_")
    if not name:
        raise ValueError("템플릿 이름이 비어 있음")
    old = get_template(name)
    t = Template(
        name=name,
        description=description or (old.description if old else ""),
        category=category if category in CATEGORIES else (old.category if old else "기타"),
        note_type=note_type if note_type in ("강의", "실습") else (old.note_type if old else ""),
        title_rule=title_rule or (old.title_rule if old else "N일차"),
        body=body.strip() or (old.body if old else ""),
        topic=topic or (old.topic if old else ""),
    )
    _write(t)
    return t

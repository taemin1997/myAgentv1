# CLAUDE.md — myAgent 작업 안내

이 저장소에서 일하는 Claude가 처음에 읽는 파일. 어느 PC에서 열든 여기 적힌 기준대로 이어서 작업함.
기능 설명·사용법은 `README.md`, 이 파일은 **작업 규칙과 지금까지 정한 것**만 담음.

> **이 파일은 계속 갱신함.** 기능을 추가·변경하거나, 사용자와 새로 정한 규칙·결정이 생기면
> 커밋하기 전에 아래 해당 칸(결정 사항, 고도화 현황, 변경 기록)을 같이 고칠 것.
> 개인정보(이메일, 전화번호, 팀원 실명 외 개인 정보)와 키 값은 절대 쓰지 않음 (공개 저장소).

## 무엇을 하는 앱인가

- OpenAI function calling 기반 데스크톱 개인 비서 (CustomTkinter GUI + PyInstaller exe)
- 주 용도: 부트캠프(SKN AI 35기, 2026-07-07 ~ 12-30) 공부 기록을 Notion DB에 정리·검색·복습·갱신
- Notion DB: "SKN AI 35기 부트캠프 정리용" (data source id는 `.env.example`)
  - 탭 = `분류` 속성: 일차 노트 / 회고 / 프로젝트 / 면접 (+기타)
  - 매일 하는 일: 그날 자료(회의록·기획서·HTML·이미지)를 앱에 끌어다 놓고 "오늘 자료로 최종 갱신해줘"
    → `N일차` 노트 + `최종 프로젝트 — Compare Agent 2.1` 페이지 섹션 갱신

## 사용자 / 말투

- 데이터·AI 직무 준비 중인 부트캠프 수강생. 목표는 "AI가 짠 코드를 읽고, 판단하고, 고칠 수 있는 능력"
- 설명·문서·주석은 한국어 반말체 서술형(~임/~함, 명령조 어미 X). 문법보다 **흐름과 이유** 위주
- 코드에는 "왜 이렇게 하는지" 주석을 충분히
- 토큰·비용을 아끼는 걸 중요하게 봄. 큰 일회성 작업은 별도 세션으로 빼자고 하는 편

## 꼭 지킬 것

1. **`.env` 값(OPENAI_API_KEY, NOTION_TOKEN, GITHUB_TOKEN)은 어떤 형태로도 출력하지 않음.**
   확인은 존재·길이·해시만. (예전에 마스킹 정규식이 실패해 키가 출력돼서 교체한 적 있음)
2. 사용자가 PC를 쓰는 동안 마우스·키보드 GUI 자동화 금지 (예전에 사용자 VS Code에 타이핑한 사고).
   exe 확인은 프로세스·창 제목만 보거나, 핸들러를 코드로 직접 호출해서 테스트. 실제 클릭·드래그는 사용자에게 부탁
3. Notion에 쓰는 기능을 테스트할 땐 **임시 페이지**(`__…(자동 삭제)`)를 만들어서 하고 끝나면 휴지통으로.
   실제 페이지(특히 최종 프로젝트 페이지)로 실험하지 않음
4. 최종 프로젝트 페이지의 섹션 제목(`## 1. 개요` … `## 9. 진행 기록`, `## 면접 예상 질문`)은
   `update_page_section`이 섹션을 찾는 기준이라 바꾸지 않음
5. 자료에 없는 사실·수치를 지어내지 않음. 본인 역할처럼 모르는 칸은 '(직접 작성)'
6. exe 다시 빌드할 땐 실행 중인 `PersonalAssistant.exe`를 먼저 종료 (`Stop-Process`)

## 개발 환경 메모 (Windows)

- uv + Python 3.12. 실행 `uv run app.py` 또는 `scripts\개인비서.bat`, 빌드 `scripts\build.bat` (→ `dist\`)
- 사용자 폴더 경로에 한글이 있어서 `cmd /c build.bat`은 깨짐 → PowerShell에서 `& .\scripts\build.bat`. .bat 파일 내용은 ASCII만
- 폴더 구조 (2026-11-14 정리): `core/`(agent·toolkit·paths·storage) · `tools/`(life·github·notion/{client,blocks,cache,history,tools})
  · `study/`(curriculum·templates·attachments) · `ui/`(web_app·web/·ctk_app·note_cards) · `scripts/`(bat) · `tests/`.
  진입점 `app.py`·`main.py`만 맨 위.
  import 는 저장소 맨 위 기준 절대 경로 (`from core.paths import …`, `import tools.notion as notion_tools`).
  `core/paths.py`의 APP_DIR = 저장소 맨 위 (exe면 exe 폴더) → `.env`·`data/`·`prompts/`는 맨 위에 둠
- 테스트할 때 Notion 함수를 가짜로 바꾸려면 **그 함수를 쓰는 모듈 쪽 이름**을 바꿔야 함
  (예: `start_background_sync`는 cache·history·tools 세 모듈에 각각 import 돼 있음)
- 자동 테스트: `uv run pytest -q` (약 6초). `tests/conftest.py`가 data/ 파일을 임시 폴더로 돌리고 네트워크 요청을 막음
  → 진짜 Notion·GitHub·OpenAI를 안 건드림. Notion은 `FakeNotion`(after 응답에 뒤쪽 블록 섞어 주는 버릇까지 흉내),
  OpenAI는 `test_agent.py`의 `FakeClient`. 코드 고친 뒤·커밋 전에 돌릴 것
- data/ 의 JSON 파일은 `core/storage.py`(`read_json`/`write_json`/`update_json`)로만 읽고 씀.
  `write_text`로 바로 덮어쓰면 동기화 스레드와 겹칠 때 반쯤 쓴 파일을 읽거나, 꺼질 때 파일이 깨짐
  (깨진 파일은 `.broken`으로 옮기고 빈 값으로 시작). 노션 캐시 저장은 파일 쪽이 더 최신(edited)이면 그걸 살림
- PowerShell 안에서 한글 섞인 파이썬 한 줄 실행은 따옴표가 잘 깨짐 → 스크립트 파일로 써서 `.venv\Scripts\python.exe -I 파일`
  (출력은 `sys.stdout.reconfigure(encoding="utf-8")`)
- 다른 PC에서 처음: `git clone` → `uv sync` → `.env.example`을 `.env`로 복사해 키 입력 → 첫 실행 때 Notion 캐시 약 10분
- Notion API 버전 `2025-09-03`, 초당 약 3회 제한 (동시 요청 늘려도 안 빨라짐)
- GitHub API는 토큰 없이 **시간당 60번** (같은 PC의 앱과 테스트가 한도를 나눠 씀). 테스트는 `_get`을 흉내 낸 가짜로 먼저 하고,
  실제 호출 테스트는 최소로. 한도를 다 쓰면 한 시간 동안 사용자 앱의 커밋 조회도 막힘
- 모델은 저장소를 `myAgentv1`처럼 이름만 넘기기도 함 → `resolve_repo`가 owner를 찾아 붙임 (내 포크면 원본 팀 저장소로)
- Notion 블록을 `after`로 끼워 넣으면 응답 `results`에 **뒤쪽 기존 블록까지 섞여 옴** → 새 블록 id는
  넣기 전·후 자식 목록 비교로 구함 (`_append`). 이걸 모르고 results를 쓰면 되돌리기가 멀쩡한 섹션을 지움

## 지금까지 정한 것 (결정 사항)

- 채팅 답은 스트리밍으로 표시 (GUI만. 터미널 버전·정리 카드·테스트는 `on_text` 없이 한 번에 받음).
  화면은 조각마다가 아니라 0.1초마다 한 번 다시 그림 (매번 그리면 버벅임)

- 모델: 평소 `gpt-5.4-mini`, 최종 프로젝트 갱신 중에만 `AGENT_MODEL_HEAVY`(기본 `gpt-5.4`).
  `get_note_template('최종갱신')`이나 `update_page_section` 호출 때 전환, 반영을 마치면 mini로 복귀 (`agent.pick_model`)
- 정리 방식은 대화 기록이 아니라 `prompts/*.md` 템플릿으로 저장 (시스템 프롬프트엔 이름만 → 토큰 절약)
- 비용: 모델을 부르는 곳은 전부 `usage.record(model, response.usage)`를 같이 부름 (새로 모델 호출을 추가할 때도).
  스트리밍은 `stream_options={"include_usage": True}`가 있어야 usage가 옴. 가격표는 2026-11-17 OpenAI 모델 페이지 기준,
  모르는 모델은 비용 0(토큰만). 오래된 대화 요약은 질문 앞에서만 자르고(도구 호출·결과 쌍 보호), 갱신 중(heavy)엔 안 함,
  줄일 옛 부분이 기준의 1/3보다 작으면 안 함(매 턴 반복 요약 방지)
- 템플릿 드롭다운은 Notion 탭과 같은 이름·아이콘·순서 (📅 일차 노트 · 강의/회의록, 📝 회고, 🚀 프로젝트, 🎯 면접)
- 첨부: 문서는 텍스트로 뽑아서, 이미지는 그림 그대로(멀티모달). 새 첨부 때 예전 첨부 본문·이미지는 기록에서 뺌
- 내가 한 일의 근거는 팀 저장소 **모든 브랜치**의 내 커밋 (`get_my_commits`, 기본 SKN35-FINAL-4TEAM / taemin1997)
- 앱이 한 Notion 수정은 `data/update_history.json`에 질문 단위(batch)로 백업 → `undo_last_update`로 통째로 되돌림
- 대청소성 일회 작업(여러 페이지 일괄 수정, 표·토글 많은 페이지 새로 쓰기)은 앱이 아니라 Claude의 Notion 커넥터로 직접

## 고도화 현황

| # | 항목 | 상태 |
|---|---|---|
| 0 | 폴더 정리 (core/tools/study/ui/scripts, Notion 모듈 5개로 분리) | ✅ 2026-11-14. 자동 테스트(`tests/` 32개)·저장 잠금(`core/storage.py`) ✅ 2026-11-17 |
| 1 | GitHub 커밋으로 '내가 한 일' 채우기 | ✅ 2026-11-14 (`tools/github.py`) |
| 2 | 갱신 백업 + 되돌리기 | ✅ 2026-11-14 (`undo_last_update`) |
| 3 | 노트 의미 검색 (임베딩, 노트 RAG) | 후보 — 포트폴리오용으로도 좋음 |
| 4 | 알아서 챙기기 (오늘 노트 없음, 복습 알림, 금요일 회고 초안 제안) | 후보 |
| 5 | 모의 면접 모드 (`면접 예상` 페이지로 출제·채점) | 후보 |
| 6 | 비용 관리 (오래된 대화 요약, 창에 비용 표시) | ✅ 2026-11-17 (`core/usage.py`, `agent.compact_history`, 웹 화면 상단 비용) |
| 7 | 채팅 화면: 스트리밍 / 마크다운 렌더링 | ✅ 스트리밍 2026-11-14, 마크다운 렌더링 2026-11-16 (웹 화면 `markdown.js`) |
| 8 | **웹 화면 이식 + 도구별 담당 캐릭터** | ① 웹 화면 ✅ 2026-11-16 (실제 사용 확인 전). ②③ 캐릭터는 **보류** (사용자 결정) |

남은 순서 (2026-11-17 정함, 캐릭터 제외): 웹 화면 마무리(사용 확인 → 예전 화면 삭제) → 89일차 갱신 결과 점검
→ ~~6 비용 관리~~ → 4 알아서 챙기기 → 3 노트 의미 검색 → 5 모의 면접.
에이전트 평가(도구 선택·인자·갱신 안전성·근거성·비용)는 `tests/`의 가짜 도구 틀을 그대로 써서 만들 예정

## 다음 작업 계획: 웹 화면 + 도구별 담당 캐릭터

사용자가 지금 디자인(CustomTkinter 기본 모양)을 마음에 안 들어 함. 도구마다 담당 캐릭터를 두고,
그 도구가 돌 때 캐릭터가 나와서 말하는 화면을 원함. 순서:

**① 웹 화면으로 이식 (pywebview + HTML/CSS/JS)** — ✅ 2026-11-16 구현. 실제로 정한 방식:
- `ui/web_app.py`의 `Api`가 JS에 노출됨 (**'_' 안 붙인 속성·메서드는 전부 노출**되니 상태 값은 꼭 `_`로).
  파이썬 → 화면은 `evaluate_js` 대신 JS가 0.1초마다 `api.poll()`로 이벤트를 한꺼번에 가져감 (0.1초 묶음 원칙 그대로)
- 끌어다 놓기는 파이썬 쪽 DOM 이벤트(`window.dom.document.events.drop`)로 받아야 `pywebviewFullPath`(실제 경로)가 있음
- 마크다운은 외부 라이브러리 없이 `ui/web/markdown.js` (HTML 먼저 이스케이프 → 태그). 링크는 기본 브라우저로 엶
- 화면만 고칠 땐 `.claude/launch.json`의 `web-ui-mock`(http.server)으로 `index.html?mock` 열면 가짜 api로 확인 가능.
  실제 창 확인은 `create_window(hidden=True)` + `evaluate_js`로 (사용자 화면에 안 뜸)
- `uv run app.py --ctk` 또는 `.env`의 `UI=ctk`면 예전 화면. pywebview import 실패해도 예전 화면으로
- 남은 것: 실제 사용 확인(끌어다 놓기·📎·카드 저장·키 입력), 안정되면 `ctk_app.py`·`note_cards.py`·tkinter 의존성 삭제

원래 계획:
- 기능은 지금과 똑같이 유지: 채팅·스트리밍, 첨부(📎 + 끌어다 놓기, 여러 개·폴더), 템플릿 드롭다운 + 정리 카드,
  새 대화 / Notion 토큰 / API 키 버튼, 상태줄(생각 중·Notion 동기화 진행률), ⚡ 상위 모델 전환 표시
- 파이썬 로직(`core/`, `tools/`, `study/`)은 그대로 쓰고 화면만 교체 → 새 화면은 `ui/web/` 같은 곳에
- 마크다운 렌더링(표·코드 블록·체크박스)은 웹에서 처리 → `pretty_markdown` 꼼수 없앰
- 스트리밍: 파이썬 → JS로 조각 전달 (`window.evaluate_js` 또는 pywebview js_api 폴링). 0.1초 묶음 갱신 원칙 유지
- 끌어다 놓기: 웹 drop 이벤트로 받은 파일 경로를 파이썬 `load_file`로 (pywebview에서 실제 경로 얻는 방법 확인 필요)
- PyInstaller로 exe 빌드 유지 (pywebview는 Windows에서 Edge WebView2 사용). `scripts/build.bat` 수정
  (HTML·CSS·JS 파일을 exe에 넣으려면 `--add-data` 필요)
- 기존 `ui/ctk_app.py`(CustomTkinter)는 새 화면이 안정될 때까지 남겨 둠. `app.py`가 어느 화면을 띄울지 고르게

**② 캐릭터 칸 시스템** (보류 중. 다시 시작할 때 아래 규칙 먼저)
- 공개 저장소(코드·주석·CLAUDE.md·커밋 메시지)에는 **칸 이름만** (`main`, `note_search`, `note_save`, `project`, `commits`, `life`,
  나중에 `proactive`). 칸 → 캐릭터 이름 연결은 비공개 `characters.json`에만
- 기본 오리지널 캐릭터는 `ui/web/characters_default/`, 개인 캐릭터는 `characters/`(gitignore, 비공개 저장소 clone).
  `.env`의 `CHARACTER_SET=default|personal`로 전환, 개인 폴더 없으면 기본으로
- 도구 결과 한 줄 요약은 모델 추가 호출 없이 규칙 기반 (비용)
| 칸 | 맡는 도구 |
|---|---|
| 비서(메인) | 최종 답변, 일반 대화 |
| 노트 검색 | `search_study_notes`, `read_study_note`, `list_study_notes`, 퀴즈 |
| 노트 정리·저장 | `save_study_note`, `get_note_template`, `save_note_template`, 정리 카드 |
| 프로젝트 갱신 | `update_page_section`, `undo_last_update` |
| 커밋 조사 | `get_my_commits` |
| 일정·생활 | `get_curriculum`, 할 일, 메모, 시간, 날씨, 계산 |
- 도구가 돌 때 `🔧 이름 {인자}` 대신 담당 캐릭터 얼굴 + 짧은 한 줄 (도구 결과를 보고 만든 요약, 예: "22일차에서 찾았어")
- 캐릭터 정의: `characters/characters.json` (칸 → 이름, 이미지 파일(표정별: 기본/생각 중/완료/오류), 색, 말버릇)
- 저장소에는 **기본 오리지널 캐릭터**(직접 만든 단순 아이콘·SVG)만 넣음 → 다른 사람이 받아도 바로 동작
- 설정 하나로 기본 캐릭터 ↔ 개인 캐릭터 전환 (스크린샷·시연용)

**③ 개인 캐릭터 = 비공개 저장소**
- 사용자는 게임 캐릭터(넥슨 IP)를 개인용으로 쓰고 싶어 함. 넥슨 게임 IP 사용 가이드 ⑤-2: 비영리 앱도 공개하려면
  고객센터 개별 문의 필요 → **공개 저장소(`myAgentv1`)·포트폴리오·스크린샷에는 절대 넣지 않음**
- 개인 캐릭터 폴더는 별도 **비공개** 저장소 `myAgent-characters`로 PC 간 동기화. 앱 저장소에서는 `characters/`를 `.gitignore`
- 이미지·대사는 사용자가 직접 준비해서 넣음. Claude는 게임 이미지·팬아트를 받아오거나, 게임 캐릭터의 대사·말투를
  흉내 내서 공개 저장소에 쓰지 않음 (폴더 형식·이미지 규격 안내까지만)
- 팬아트를 쓰려면 넥슨과 별개로 그린 사람 허락 필요 (가이드 ⑥-1). 공개하고 싶어지면 넥슨 1:1 문의 후 결정

확인할 것: 2026-11-16(89일차) 첫 실제 갱신 결과 — 기존 내용 유실, 엉뚱한 섹션 수정, 결정 사항 누락이 없는지.
문제가 있으면 `AGENT_MODEL_HEAVY` 상향 검토.

## 변경 기록

- 2026-11-14: 첫 커밋 (개인 비서 + Notion 공부 비서, PDF → 노트 카드, 커리큘럼 연동)
- 2026-11-14: 템플릿(prompts/), 최종 프로젝트 섹션 갱신, 표·체크박스 변환, 첨부 형식 확대(docx·pptx·HTML·이미지), 끌어다 놓기, 갱신 전용 모델
- 2026-11-14: 내 커밋 가져오기(`get_my_commits`), 갱신 백업·되돌리기(`undo_last_update`), CLAUDE.md 추가
- 2026-11-14: 저장소 이름만 줘도 찾기(`resolve_repo`: 내 저장소 → 포크면 원본 → 조직), 날짜 오류 메시지 분리
- 2026-11-14: 채팅 답 스트리밍 (`agent._ask_model` stream=True, 도구 호출 조각 조립 / `app._poll_events`가 0.1초마다 말풍선 갱신)
- 2026-11-14: 다음 작업(웹 화면 + 도구별 담당 캐릭터, 개인 캐릭터는 비공개 저장소) 계획 정리
- 2026-11-14: 폴더 정리 — core/ tools/ study/ ui/ scripts/, notion_tools.py(950줄)를 tools/notion/ 5개 모듈로. 동작 변화 없음
- 2026-11-16: 웹 화면 (pywebview, `ui/web_app.py` + `ui/web/`). 기능은 예전 화면과 같음 + 마크다운 렌더링·여러 줄 입력·
  카드 미리보기. 예전 화면은 `--ctk`로 남김. `save_env`는 `core/paths.py`로 옮김. 캐릭터 단계는 보류
- 2026-11-17: 자동 테스트(pytest, `tests/` 32개: 섹션 갱신·되돌리기, 저장 잠금, 에이전트 루프·스트리밍, 웹 Api 등)와
  `core/storage.py`(임시 파일 → 바꿔치기, 파일별 잠금). 캐시·되돌리기 기록·메모·할 일 저장을 전부 이걸로 바꿈
- 2026-11-17: 비용 관리 — 토큰·비용 기록(`core/usage.py`, `data/usage.json`), 웹 화면 상단에 이번 대화·오늘 비용,
  긴 대화는 오래된 부분을 요약(`agent.compact_history`, 기본 24,000토큰 넘으면). 테스트 42개

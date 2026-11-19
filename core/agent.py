"""에이전트 핵심 로직. CLI(main.py)와 GUI(app.py)가 함께 사용한다.

에이전트 루프:
  1. 모델에게 (대화 기록 + 도구 목록) 전송
  2. 모델이 도구 호출(tool_calls)을 요청하면 → tools.py 함수 실행 →
     결과를 기록에 추가하고 1번으로 돌아감
  3. 도구 호출 없이 답하면 그게 최종 답변
"""

import os
from typing import Callable

import openai
from dotenv import load_dotenv

import tools.notion as notion_tools
from core import usage
from core.paths import ENV_FILE
from study import templates
from study.attachments import build_user_content
from tools import TOOL_SCHEMAS, run_tool

load_dotenv(ENV_FILE)  # .env 파일의 OPENAI_API_KEY 를 환경변수로 불러옴

MODEL = os.getenv("AGENT_MODEL", "gpt-5.4-mini")
# 최종 프로젝트 페이지 갱신(긴 페이지 읽기 → 바뀐 섹션 고르기 → 기존 내용 살려서 합치기)처럼
# 판단할 게 많은 작업만 상위 모델로. 평소 대화·검색·정리는 싼 MODEL 그대로.
HEAVY_MODEL = os.getenv("AGENT_MODEL_HEAVY", "gpt-5.4")
HEAVY_WINDOW = 12  # 최근 메시지 몇 개 안에 갱신 작업 흔적이 있으면 상위 모델 유지 ("응 반영해" 같은 다음 턴까지)
MAX_STEPS = 15  # 질문 하나에 도구 호출을 반복하는 최대 횟수 (무한루프 방지)
# 대화 기록이 이만큼(추정 토큰) 넘으면 오래된 부분을 요약해서 줄임. 매 질문마다 기록 전체를 다시 보내서
# 기록이 길수록 질문 하나의 입력 비용이 계속 커지기 때문. 최근 KEEP_RECENT_TURNS 개 질문은 원문 그대로 둠
SUMMARY_TRIGGER = int(os.getenv("SUMMARY_TRIGGER_TOKENS", "24000"))
KEEP_RECENT_TURNS = 4
SUMMARY_HEAD = "[이전 대화 요약] 오래된 대화를 줄인 것. 필요하면 이 내용을 이어서 쓸 것.\n"

# 노트 정리 형식(강의노트·회의록·주간회고 …)은 templates.py → prompts/*.md 파일에 따로 저장함.
# 시스템 프롬프트에는 이름만 넣고 본문은 정리할 때만 불러서, 매 요청마다 붙는 토큰을 줄임.
templates.ensure_defaults()

SYSTEM_PROMPT = """당신은 사용자의 개인 비서이자 공부 도우미입니다. 한국어로 친근하고 간결하게 답하세요.

[사용자]
SK AI 캠프(SKN AI 35기) 부트캠프 수강생이고 12월 수료 예정. 데이터·AI 직무를 준비 중.
배운 내용은 Notion 'SKN AI 35기 부트캠프 정리용' DB에 'N일차' 제목으로 정리해 둠.
목표는 "AI가 짠 코드를 읽고, 판단하고, 고칠 수 있는 능력". 문법 암기보다 흐름과 이유 위주 설명을 좋아함.

커리큘럼: 2026-07-07 ~ 12-30, 1~120일차. 'N일차' = 커리큘럼 일차 번호. 일정·과목·프로젝트 질문은 get_curriculum.

[공부 비서 규칙]
- 배운 개념을 물으면 먼저 search_study_notes로 사용자의 노트를 찾아보고, 노트 내용을 바탕으로
  설명하세요. 어느 노트(N일차)에서 나온 내용인지 같이 알려주세요. 노트에 없으면 없다고 말하고 일반 지식으로 설명하세요.
- 복습 퀴즈를 요청받으면 read_study_note로 해당 노트를 읽고 문제를 내세요.
  한 번에 3~5문제, 개념 이해를 묻는 문제 위주(단순 암기 X). 정답은 바로 공개하지 말고
  사용자가 답하면 채점하고 왜 맞고 틀렸는지 설명하세요. 다 풀면 mark_note_reviewed를 할지 물어보세요.
- 노트 정리/저장을 요청받으면 반말체(~임/~함, 명령조 어미 X)로 마크다운을 만들고,
  저장 전에 제목과 내용을 먼저 보여준 뒤 확인을 받고 save_study_note로 저장하세요.
- 코드 예시는 "왜 이렇게 하는지" 주석을 달아주세요.

[자료로 노트 만들기]  정리 방식(템플릿)이 저장돼 있음: 강의노트(강의 PDF), 회의록(프로젝트 회의록·기획 자료),
프로젝트(미니프로젝트 1~4 같은 프로젝트 소개 페이지), 주간회고(한 주 정리), 면접(면접 예상 질문·답변),
최종갱신(작업 순서) + 사용자가 추가한 것. Notion DB 탭 = 분류: 일차 노트 / 회고 / 프로젝트 / 면접.
최종 프로젝트 자료(회의록·기획서 등)를 주면서 정리·갱신을 부탁하면 get_note_template('최종갱신')을 먼저 부르고
그 순서대로 진행 (그날 일차 노트 + 내 커밋(get_my_commits) 확인 + 최종 프로젝트 페이지 섹션 갱신).
팀 GitHub에 이슈를 써 달라고 하면 draft_github_issue로 초안만 만듦 (화면 카드에서 사용자가 직접 올림. 올렸다고 말하지 말 것).
앱이 한 Notion 수정이 잘못됐다고 하면('되돌려줘', '원래대로') undo_last_update로 직전 질문의 수정을 통째로 되돌림.
save_study_note로 DB에 어떤 종류의 페이지든 새로 만들 수 있음 (일차 노트·회고·프로젝트·면접). 자료가 없어도
'템플릿만', '틀만' 만들어 달라면 해당 템플릿의 섹션 제목 + '(작성 예정)' 칸으로 빈 틀 페이지를 만들 것
(빈 틀은 확인 없이 바로 만들어도 됨). 못 만든다고 하지 말 것. 이미 저장한 페이지를 다시 저장하지 말 것.
메시지에 [첨부 파일: ...] <<< 본문 >>> 이나 [첨부 이미지: ...](그림 같이 옴)가 붙어 오거나 정리·페이지 생성을 요청하면:
(이미지는 그림 속 글자·도형·화살표까지 읽어서 반영. 여러 파일이 같이 오면 한 날의 자료로 보고 합쳐서 정리)
1. 자료 종류에 맞는 템플릿을 고르고 get_note_template으로 불러서 그 형식대로 정리하세요.
   애매하면 어느 템플릿으로 할지 물어보세요. 83일차(11/6)부터는 최종 프로젝트 기간이라 회의록이 많음.
2. 제목: 템플릿의 제목 규칙대로. 'N일차'는 사용자가 말 안 했으면 list_study_notes(limit=3)로 마지막 번호를
   확인하고 다음 번호를 제안하세요 (밀린 날을 채우는 중일 수 있으니 번호는 꼭 확인).
3. 정리본 전체를 먼저 보여주고 맨 마지막에 한 번만 "N일차로 저장할까?"라고 물은 뒤, 승인하면
   save_study_note로 저장하세요. 템플릿의 분류(category)·유형(note_type)과 날짜를 같이 넣으세요.
4. 사용자가 "앞으로 이렇게 정리해줘", "이 형식 기억해"라고 하면 save_note_template으로 정리 방식을 저장하세요.
5. 스캔 PDF처럼 첨부 텍스트가 거의 없으면 읽을 수 없다고 솔직히 말하고, 페이지를 캡처해서 이미지로 첨부하라고 안내.
6. 파일이 여러 개면 기본은 하루치 노트 하나로 합쳐서 정리하세요. 사용자가 날짜별로 따로 원할 때만,
   첨부창의 '하루치로 합치기'를 끄고 '📝 노트로 정리'를 누르면 파일마다 카드가 따로 생긴다고 안내하세요.

[생활 비서 규칙]
- 날짜/시간이 관련되면 get_current_time으로 실제 시간을 확인하세요.
- 계산은 암산하지 말고 calculate 도구를 쓰세요.
- 사용자가 "기억해", "메모해" 같은 말을 하면 save_note로 저장하고,
  예전에 말한 내용을 물으면 search_notes로 찾아보세요.
- 할 일 관련 요청은 add_todo / list_todos / complete_todo를 쓰세요.
  "내일", "다음 주 금요일" 같은 마감일은 현재 날짜를 확인한 뒤 YYYY-MM-DD로 바꿔 저장하세요.
- 도구 결과를 그대로 붙여넣지 말고, 사람이 읽기 좋게 정리해서 알려주세요."""


def new_conversation() -> list:
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def _is_heavy_call(name: str, arguments: str) -> bool:
    """상위 모델로 넘어가는 신호: 최종갱신 템플릿을 불렀거나, 기존 페이지 섹션을 고치는 도구를 씀."""
    return name == "update_page_section" or (name == "get_note_template" and "최종갱신" in arguments)


def pick_model(messages: list) -> str:
    """갱신 작업 중이면 HEAVY_MODEL, 아니면 MODEL.

    갱신은 '자료 읽고 계획 보여주기 → 사용자 확인("응 반영해") → 반영'으로 여러 턴에 걸쳐서,
    최근 메시지를 순서대로 훑으며 상태를 추적함:
      - 최종갱신 템플릿 호출 또는 섹션 수정 → 갱신 중
      - 섹션 수정(update_page_section)까지 한 뒤 모델이 답을 마치면 → 갱신 끝, 다시 싼 모델
    (갱신을 하다 말고 딴 얘기를 하면 HEAVY_WINDOW 만큼 지나서 자연히 풀림)"""
    heavy = applied = False
    for m in messages[-HEAVY_WINDOW:]:
        calls = m.get("tool_calls") or []
        for call in calls:
            name, args = call["function"]["name"], call["function"]["arguments"]
            if _is_heavy_call(name, args):
                heavy = True
            if name == "update_page_section":
                applied = True
        if m.get("role") == "assistant" and not calls and applied:  # 반영까지 끝내고 답한 턴
            heavy = applied = False
    return HEAVY_MODEL if heavy else MODEL


def _ask_model(client: openai.OpenAI, model: str, messages: list,
               on_text: Callable[[str, str], None] | None) -> tuple[str, str, list[dict]]:
    """모델 한 번 호출 → (본문, 거절 문구, 도구 호출 목록).

    on_text 가 있으면 스트리밍으로 받음: 글자가 생기는 대로 on_text("delta", 조각)을 부르고,
    이 단계 글이 끝나면 on_text("end", 전체 글). 화면은 그걸 받아서 말풍선에 바로바로 이어 씀.
    스트리밍에선 도구 호출도 조각(이름·인자 JSON 일부)으로 나눠 오니까 index 별로 이어 붙여서 완성함.
    on_text 가 없으면(터미널 버전·테스트) 예전처럼 한 번에 받음."""
    if on_text is None:
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS)
        usage.record(model, getattr(response, "usage", None))  # 토큰·비용 기록 (core/usage.py)
        msg = response.choices[0].message
        calls = [{"id": c.id, "type": "function",
                  "function": {"name": c.function.name, "arguments": c.function.arguments}}
                 for c in msg.tool_calls or []]
        return msg.content or "", msg.refusal or "", calls

    text, refusal, calls = [], [], {}
    # include_usage: 스트리밍은 이걸 줘야 마지막 조각에 토큰 사용량이 붙어 옴 (안 주면 비용을 알 수 없음)
    stream = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS, stream=True,
                                            stream_options={"include_usage": True})
    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage.record(model, chunk.usage)
        if not chunk.choices:  # 마지막 사용량 조각 등은 choices 가 비어 있음
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text.append(delta.content)
            on_text("delta", delta.content)
        if getattr(delta, "refusal", None):
            refusal.append(delta.refusal)
        for tc in delta.tool_calls or []:
            # 첫 조각에 id·이름이 오고, 인자 JSON 은 여러 조각으로 나눠서 옴
            c = calls.setdefault(tc.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if tc.id:
                c["id"] = tc.id
            if tc.function and tc.function.name:
                c["function"]["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                c["function"]["arguments"] += tc.function.arguments
    if text:
        on_text("end", "".join(text))
    return "".join(text), "".join(refusal), [calls[i] for i in sorted(calls)]


def run_turn(
    client: openai.OpenAI,
    messages: list,
    on_tool: Callable[[str, str], None] | None = None,
    on_text: Callable[[str, str], None] | None = None,
) -> str:
    """도구 호출이 끝날 때까지 모델과 주고받고 최종 답변을 돌려준다.
    messages 는 이 과정에서 계속 늘어난다 (= 대화 기록).
    on_tool(이름, 인자JSON) 은 도구를 실행할 때마다 불린다 (화면 표시용).
    on_text(종류, 글) 를 주면 답을 스트리밍으로 받음 (_ask_model 참고). 도구를 쓰기 전에 모델이
    "확인해볼게" 같은 말을 먼저 하면 그것도 따로 흘러나옴.

    모델은 매 단계 다시 고름: 처음엔 싼 모델로 시작했다가, 모델이 get_note_template('최종갱신')을
    부르는 순간 다음 단계부터 상위 모델로 넘어감 (무슨 작업인지 판단 자체는 싼 모델이 해도 충분해서)."""
    current = None
    notion_tools.begin_batch()  # 이 질문에서 한 Notion 수정은 한 묶음 → "되돌려줘" 한 번에 같이 되돌림
    compact_history(client, messages, on_tool)  # 기록이 길면 오래된 부분 요약 (비용 절약)
    for _ in range(MAX_STEPS):
        model = pick_model(messages)
        if model != current and model == HEAVY_MODEL and on_tool:
            on_tool("⚡ 갱신 작업이라 상위 모델로 전환", model)
        current = model
        content, refusal, calls = _ask_model(client, model, messages, on_text)

        # 모델의 응답(도구 호출 요청 포함)을 기록에 남김
        assistant = {"role": "assistant", "content": content or None}
        if calls:
            assistant["tool_calls"] = calls
        messages.append(assistant)

        if not calls:  # 도구를 더 안 쓰면 최종 답변
            return content or refusal or ""

        # 요청받은 도구를 모두 실행하고 결과를 기록에 추가
        for call in calls:
            name, args = call["function"]["name"], call["function"]["arguments"]
            if on_tool:
                on_tool(name, args)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": run_tool(name, args)})

    return "(도구 호출이 너무 많아 중단했어요. 질문을 나눠서 해 주세요.)"


def estimate_tokens(messages: list) -> int:
    """기록의 토큰 수 어림값. 정확히 세려면 토크나이저가 필요한데, 요약할지 말지 정하는 데는 어림으로 충분함.
    한국어는 대략 1~1.5글자, 영어는 약 4글자가 토큰 1개라서 섞인 글 기준 '글자 수 / 2' 로 봄. 이미지는 장당 1000."""
    chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            chars += sum(len(p.get("text", "")) for p in c) + 2000 * sum(p.get("type") == "image_url" for p in c)
        chars += sum(len(call["function"]["arguments"]) for call in m.get("tool_calls") or [])
    return chars // 2


def _transcript(messages: list, per_message: int = 1500) -> str:
    """요약용으로 기록을 사람이 읽는 대화 형태로. 도구 결과는 길어서 앞부분만."""
    lines = []
    for m in messages:
        role, c = m.get("role"), m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "") for p in c if p.get("type") == "text") + "\n(이미지 첨부)"
        c = (c or "")[: per_message if role != "tool" else 500]
        if role == "assistant" and m.get("tool_calls"):
            calls = ", ".join(f"{t['function']['name']}({t['function']['arguments'][:200]})" for t in m["tool_calls"])
            lines.append(f"[비서 → 도구] {calls}" + (f"\n[비서] {c}" if c else ""))
        elif c:
            lines.append({"user": "[사용자]", "assistant": "[비서]", "tool": "[도구 결과]",
                          "system": "[이전 요약]"}.get(role, f"[{role}]") + " " + c)
    return "\n".join(lines)


def compact_history(client: openai.OpenAI, messages: list, on_tool: Callable | None = None) -> bool:
    """기록이 SUMMARY_TRIGGER 보다 길면 오래된 부분을 싼 모델로 요약해서 시스템 메시지 하나로 바꿈.

    - 자르는 곳은 사용자 질문 바로 앞 → 도구 호출(tool_calls)과 그 결과(tool)가 갈라지지 않음
      (갈라지면 OpenAI 가 '결과 없는 도구 호출' 오류를 냄)
    - 최종 프로젝트 갱신 중(상위 모델 구간)엔 줄이지 않음: 계획 → "응 반영해" 맥락이 끊기면 엉뚱하게 고칠 수 있어서
    - 요약이 실패해도 대화는 그대로 진행 (요약은 비용 절약용이지 꼭 필요한 일이 아님)
    돌려주는 값: 줄였으면 True."""
    before = estimate_tokens(messages)
    if before < SUMMARY_TRIGGER or pick_model(messages) == HEAVY_MODEL:
        return False
    user_at = [i for i, m in enumerate(messages) if i > 0 and m.get("role") == "user"]
    if len(user_at) <= KEEP_RECENT_TURNS:
        return False
    cut = user_at[-KEEP_RECENT_TURNS]
    # 줄일 옛 부분이 작으면 안 함: 최근 질문들만으로 기준을 넘는 경우(큰 첨부 등) 매 질문마다
    # '요약 + 한 턴'을 또 요약하느라 요약 호출 비용만 계속 드는 걸 막음
    if estimate_tokens(messages[1:cut]) < SUMMARY_TRIGGER // 3:
        return False
    try:
        response = client.chat.completions.create(model=MODEL, messages=[
            {"role": "system", "content":
                "사용자(부트캠프 수강생)와 개인 비서의 예전 대화를 이어서 대화할 수 있게 요약함. 한국어, 반말체(~임/~함), 목록으로.\n"
                "꼭 남길 것: 사용자가 정한 것·부탁한 방식, 저장·수정한 Notion 페이지 제목과 URL, 진행 중이거나 미뤄 둔 일, "
                "아직 답하지 않은 질문, 퀴즈 진행 상황. 도구 결과 원문·인사말은 빼고, 대화에 없는 내용은 절대 지어내지 말 것."},
            {"role": "user", "content": _transcript(messages[1:cut])},
        ])
        usage.record(MODEL, getattr(response, "usage", None))
        summary = (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 - 요약 실패는 무시하고 원래 기록으로 계속
        return False
    if not summary:
        return False
    messages[1:cut] = [{"role": "system", "content": SUMMARY_HEAD + summary}]
    if on_tool:
        on_tool("🗜 오래된 대화를 요약해서 줄임", f"약 {before:,} → {estimate_tokens(messages):,} 토큰")
    return True


def summarize_pdfs(client: openai.OpenAI, attachments: list, title: str, template: str = "강의노트") -> str:
    """PDF(들) → 노트 마크다운. 채팅 기록과 상관없는 1회성 호출.

    채팅으로 정리하면 PDF 본문과 정리본이 대화 기록에 계속 쌓여서 PDF가 많을수록 비싸지고,
    여러 노트 내용이 섞일 수도 있음. 그래서 '정리 카드'는 PDF(묶음)마다 새 대화로 따로 요청함.
    template: prompts 폴더의 템플릿 이름 (강의노트 / 회의록 / 주간회고 …)
    """
    t = templates.get_template(template) or templates.get_template("강의노트")
    user_content = build_user_content(f"'{title}' 노트로 정리해줘.", attachments)  # 이미지는 그림 그대로
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "부트캠프 수강생의 자료를 Notion 노트로 정리하는 도우미임.\n"
             + t.system_prompt()
             + "\n정리본 마크다운만 출력할 것. 앞뒤 인사말이나 '저장할까?' 같은 질문은 쓰지 말 것."
             + "\n첨부 이미지는 그림 속 글자·도형까지 읽어서 반영할 것."
             + "\n첨부 텍스트도 이미지도 거의 없으면 '이미지/스캔 PDF라서 내용을 읽을 수 없음' 한 줄만 출력."},
            {"role": "user", "content": user_content},
        ],
    )
    usage.record(MODEL, getattr(response, "usage", None))
    return (response.choices[0].message.content or "").strip()


def friendly_error(e: Exception) -> str:
    """API 예외를 사용자에게 보여줄 한국어 메시지로 바꾼다."""
    if isinstance(e, openai.AuthenticationError):
        return "API 키가 올바르지 않습니다. .env 를 확인하세요."
    if isinstance(e, openai.RateLimitError):
        return "요청이 너무 많거나 크레딧이 부족합니다. 잠시 후 다시 시도하세요."
    if isinstance(e, openai.APIConnectionError):
        return "네트워크 연결을 확인하세요."
    if isinstance(e, openai.APIStatusError):
        return f"API 오류 ({e.status_code}): {e.message}"
    return f"오류: {e}"

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

from paths import ENV_FILE
from tools import TOOL_SCHEMAS, run_tool

load_dotenv(ENV_FILE)  # .env 파일의 OPENAI_API_KEY 를 환경변수로 불러옴

MODEL = os.getenv("AGENT_MODEL", "gpt-5.4-mini")
MAX_STEPS = 15  # 질문 하나에 도구 호출을 반복하는 최대 횟수 (무한루프 방지)

# 강의 PDF → 노트 정리 형식. 채팅(SYSTEM_PROMPT)과 정리 카드(summarize_pdfs)가 같이 씀
NOTE_STYLE = """정리본 구조 (마크다운, 반말체 ~임/~함, 명령조 어미 X, 제목은 ###까지만):
   # 주제 한 줄 요약
   ## 오늘 배운 것 (핵심 키워드 3~6개 bullet)
   ## 주제별 정리 → 각 주제마다 "무엇인지 → 왜 필요한지 → 어떻게 동작하는지" 순서
   ## 코드 (PDF에 코드가 있으면 핵심만, 주석으로 이유 설명)
   ## 헷갈리기 쉬운 점 / 비교 (있으면)
   ## 한 줄 복습
슬라이드 문장을 그대로 베끼지 말고 이해하기 쉽게 재구성하되, PDF의 주요 주제는 빠뜨리지 말 것.
PDF에 없는 내용을 지어내지 말 것. 보충 설명을 덧붙일 땐 '(보충)'이라고 표시."""

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

[PDF로 노트 만들기]
사용자 메시지에 [첨부 PDF: ...] <<< 본문 >>> 이 붙어 오면 강의 자료임. 정리 요청이면:
1. 제목: 사용자가 일차를 말하지 않았으면 list_study_notes(limit=3)로 마지막 'N일차'를 확인하고
   'N+1일차'를 제안하세요. (밀린 날을 채우는 중일 수 있으니 번호는 꼭 사용자에게 확인)
2. """ + NOTE_STYLE + """
3. 정리본 전체를 먼저 보여주고 맨 마지막에 한 번만 "N일차로 저장할까?"라고 물은 뒤,
   사용자가 승인하면 save_study_note로 저장하세요. 강의 날짜를 알면 date도 넣으세요.
4. 첨부 텍스트가 거의 없으면 이미지/스캔 PDF라서 내용을 읽을 수 없다고 솔직히 말하세요.
5. PDF가 여러 개고 날짜별로 따로 정리하려는 거면, 첨부창의 '📝 노트로 정리' 버튼을 쓰면
   PDF마다 정리 카드가 따로 만들어진다고 안내하세요.

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


def run_turn(
    client: openai.OpenAI,
    messages: list,
    on_tool: Callable[[str, str], None] | None = None,
) -> str:
    """도구 호출이 끝날 때까지 모델과 주고받고 최종 답변을 돌려준다.
    messages 는 이 과정에서 계속 늘어난다 (= 대화 기록).
    on_tool(이름, 인자JSON) 은 도구를 실행할 때마다 불린다 (화면 표시용)."""
    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        msg = response.choices[0].message

        # 모델의 응답(도구 호출 요청 포함)을 기록에 남김
        assistant = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in msg.tool_calls
            ]
        messages.append(assistant)

        if not msg.tool_calls:  # 도구를 더 안 쓰면 최종 답변
            return msg.content or msg.refusal or ""

        # 요청받은 도구를 모두 실행하고 결과를 기록에 추가
        for call in msg.tool_calls:
            if on_tool:
                on_tool(call.function.name, call.function.arguments)
            result = run_tool(call.function.name, call.function.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return "(도구 호출이 너무 많아 중단했어요. 질문을 나눠서 해 주세요.)"


def summarize_pdfs(client: openai.OpenAI, attachments: list, title: str) -> str:
    """PDF(들) → 노트 마크다운. 채팅 기록과 상관없는 1회성 호출.

    채팅으로 정리하면 PDF 본문과 정리본이 대화 기록에 계속 쌓여서 PDF가 많을수록 비싸지고,
    여러 노트 내용이 섞일 수도 있음. 그래서 '정리 카드'는 PDF(묶음)마다 새 대화로 따로 요청함.
    """
    pdf_text = "".join(a.to_prompt() for a in attachments)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "부트캠프 강의 자료를 복습용 노트로 정리하는 도우미임.\n" + NOTE_STYLE
             + "\n정리본 마크다운만 출력할 것. 앞뒤 인사말이나 '저장할까?' 같은 질문은 쓰지 말 것."
             + "\n첨부 텍스트가 거의 없으면 '이미지/스캔 PDF라서 내용을 읽을 수 없음' 한 줄만 출력."},
            {"role": "user", "content": f"'{title}' 노트로 정리해줘." + pdf_text},
        ],
    )
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

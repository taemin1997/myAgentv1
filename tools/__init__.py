"""개인 비서 에이전트가 쓸 수 있는 도구 모음.

@tool 을 붙인 함수는 함수 시그니처(타입 힌트)와 docstring을 읽어서
모델에게 보낼 도구 설명(JSON 스키마)을 자동으로 만듦 (core/toolkit.py).
그래서 docstring의 설명과 Args 항목을 잘 써 두는 게 중요함.
새 도구를 추가하려면 알맞은 모듈에 함수를 만들고 아래 ALL_TOOLS 에 넣으면 됨.

  life.py     시간·계산·메모·할 일·날씨
  notion/     Notion 공부 노트 (검색·읽기·저장·섹션 갱신·되돌리기·템플릿·커리큘럼)
  github.py   팀 저장소의 내 커밋
"""

import json

from .github import GITHUB_TOOLS
from .life import (add_todo, calculate, complete_todo, get_current_time, get_weather, list_todos,
                   save_note, search_notes)
from .notion import NOTION_TOOLS

ALL_TOOLS = [
    get_current_time,
    calculate,
    save_note,
    search_notes,
    add_todo,
    list_todos,
    complete_todo,
    get_weather,
    *NOTION_TOOLS,  # notion_tools.py 의 공부 비서 도구들
    *GITHUB_TOOLS,  # github_tools.py: 팀 저장소의 내 커밋
]

# 모델에게 보낼 도구 목록과, 이름으로 실제 함수를 찾는 표
TOOL_SCHEMAS = [f.schema for f in ALL_TOOLS]
TOOLS_BY_NAME = {f.__name__: f for f in ALL_TOOLS}


def run_tool(name: str, arguments_json: str) -> str:
    """모델이 요청한 도구를 실행하고 결과를 문자열로 돌려준다.
    에러가 나도 프로그램이 죽지 않고, 에러 내용을 모델에게 알려준다."""
    fn = TOOLS_BY_NAME.get(name)
    if fn is None:
        return f"알 수 없는 도구: {name}"
    try:
        args = json.loads(arguments_json or "{}")
        return str(fn(**args))
    except Exception as e:
        return f"도구 실행 오류 ({type(e).__name__}): {e}"

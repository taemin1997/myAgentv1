"""@tool 데코레이터: 파이썬 함수 → OpenAI function-calling 스키마.

tools.py, notion_tools.py 가 같이 쓰려고 따로 뺀 파일.
(tools.py 안에 두면 notion_tools.py 가 tools.py 를 import 하고,
 tools.py 가 다시 notion_tools.py 를 import 하는 순환 참조가 생김)
"""

import inspect
import re
from typing import Callable

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def tool(fn: Callable) -> Callable:
    """함수에서 OpenAI function-calling 스키마를 만들어 fn.schema 에 붙인다.

    - 설명: docstring 에서 'Args:' 앞부분
    - 인자 설명: docstring 의 'Args:' 아래 '이름: 설명' 줄
    - 인자 타입: 타입 힌트 (str/int/float/bool)
    - 필수 여부: 기본값이 없으면 필수
    """
    doc = inspect.getdoc(fn) or ""
    description, _, args_part = doc.partition("Args:")
    arg_docs = dict(re.findall(r"^\s*(\w+):\s*(.+)$", args_part, re.MULTILINE))

    properties, required = {}, []
    for name, param in inspect.signature(fn).parameters.items():
        properties[name] = {
            "type": _JSON_TYPES.get(param.annotation, "string"),
            "description": arg_docs.get(name, ""),
        }
        if param.default is inspect.Parameter.empty:
            required.append(name)

    fn.schema = {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description.strip(),
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }
    return fn

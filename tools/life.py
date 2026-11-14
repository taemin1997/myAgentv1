"""생활 도구: 시간, 계산, 메모, 할 일, 날씨. (Notion·GitHub와 상관없는 기본 비서 기능)"""

import ast
import json
import operator
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.paths import DATA_DIR
from core.toolkit import tool

NOTES_FILE = DATA_DIR / "notes.json"
TODOS_FILE = DATA_DIR / "todos.json"


# ---------------------------------------------------------------- 저장 헬퍼
def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, items: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 시간
@tool
def get_current_time(timezone: str = "Asia/Seoul") -> str:
    """현재 날짜와 시간을 알려줍니다. 날짜·요일·시간 계산이 필요할 때 먼저 호출하세요.

    Args:
        timezone: IANA 시간대 이름. 예: Asia/Seoul, America/New_York.
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        return f"알 수 없는 시간대입니다: {timezone}"
    weekday = "월화수목금토일"[now.weekday()]
    return now.strftime(f"%Y-%m-%d ({weekday}) %H:%M:%S {timezone}")


# ---------------------------------------------------------------- 계산기
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("숫자와 + - * / // % ** 괄호만 쓸 수 있습니다")


@tool
def calculate(expression: str) -> str:
    """사칙연산 수식을 정확하게 계산합니다. 암산 대신 항상 이 도구를 쓰세요.

    Args:
        expression: 계산할 수식. 예: (1200 * 3) / 7, 2 ** 10.
    """
    try:
        return str(_eval(ast.parse(expression, mode="eval").body))
    except Exception as e:
        return f"계산 실패: {e}"


# ---------------------------------------------------------------- 메모
@tool
def save_note(title: str, content: str) -> str:
    """사용자가 기억해 달라고 한 내용을 메모로 저장합니다.

    Args:
        title: 메모 제목 (짧게).
        content: 메모 내용.
    """
    notes = _load(NOTES_FILE)
    notes.append({"title": title, "content": content, "created": _now()})
    _save(NOTES_FILE, notes)
    return f"메모 '{title}'을(를) 저장했습니다."


@tool
def search_notes(keyword: str = "") -> str:
    """저장된 메모를 찾습니다. keyword를 비우면 전체 메모를 보여줍니다.

    Args:
        keyword: 제목이나 내용에서 찾을 단어. 비우면 전체.
    """
    notes = _load(NOTES_FILE)
    hits = [n for n in notes if keyword in n["title"] or keyword in n["content"]]
    if not hits:
        return "일치하는 메모가 없습니다."
    return "\n".join(f"- [{n['created']}] {n['title']}: {n['content']}" for n in hits)


# ---------------------------------------------------------------- 할 일
@tool
def add_todo(task: str, due: str = "") -> str:
    """할 일 목록에 새 항목을 추가합니다.

    Args:
        task: 할 일 내용.
        due: 마감일 (YYYY-MM-DD). 없으면 빈 문자열.
    """
    todos = _load(TODOS_FILE)
    new_id = max((t["id"] for t in todos), default=0) + 1
    todos.append({"id": new_id, "task": task, "due": due, "done": False, "created": _now()})
    _save(TODOS_FILE, todos)
    return f"할 일 #{new_id} 추가: {task}" + (f" (마감 {due})" if due else "")


@tool
def list_todos(include_done: bool = False) -> str:
    """할 일 목록을 보여줍니다.

    Args:
        include_done: true면 완료한 항목도 함께 보여줍니다.
    """
    todos = [t for t in _load(TODOS_FILE) if include_done or not t["done"]]
    if not todos:
        return "할 일이 없습니다."
    return "\n".join(
        f"#{t['id']} [{'x' if t['done'] else ' '}] {t['task']}" + (f" (마감 {t['due']})" if t["due"] else "")
        for t in todos
    )


@tool
def complete_todo(todo_id: int) -> str:
    """할 일을 완료 처리합니다. 번호를 모르면 먼저 list_todos로 확인하세요.

    Args:
        todo_id: 완료할 할 일의 번호.
    """
    todos = _load(TODOS_FILE)
    for t in todos:
        if t["id"] == todo_id:
            t["done"] = True
            _save(TODOS_FILE, todos)
            return f"할 일 #{todo_id} '{t['task']}' 완료!"
    return f"#{todo_id}번 할 일을 찾지 못했습니다."


# ---------------------------------------------------------------- 날씨 (Open-Meteo, API 키 불필요)
def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


@tool
def get_weather(city: str) -> str:
    """도시의 현재 날씨와 오늘 최고/최저 기온을 조회합니다.

    Args:
        city: 도시 이름. 영어가 가장 정확합니다. 예: Seoul, Busan, Tokyo.
    """
    try:
        q = urllib.parse.urlencode({"name": city, "count": 1, "language": "ko"})
        geo = _get_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results")
        if not geo:
            return f"'{city}' 위치를 찾지 못했습니다. 영어 도시명으로 다시 시도해 보세요."
        loc = geo[0]
        q = urllib.parse.urlencode({
            "latitude": loc["latitude"], "longitude": loc["longitude"],
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto", "forecast_days": 1,
        })
        w = _get_json(f"https://api.open-meteo.com/v1/forecast?{q}")
        c, d = w["current"], w["daily"]
        return (
            f"{loc['name']}: 현재 {c['temperature_2m']}°C, 습도 {c['relative_humidity_2m']}%, "
            f"풍속 {c['wind_speed_10m']}km/h, 날씨코드 {c['weather_code']} (WMO) / "
            f"오늘 최고 {d['temperature_2m_max'][0]}°C, 최저 {d['temperature_2m_min'][0]}°C, "
            f"강수확률 {d['precipitation_probability_max'][0]}%"
        )
    except Exception as e:
        return f"날씨 조회 실패: {e}"

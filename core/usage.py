"""모델 사용량(토큰)과 비용 기록.

[어디서 숫자를 얻나]
OpenAI 응답의 usage (prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens).
스트리밍은 stream_options={"include_usage": True} 를 줘야 마지막 조각에 usage 가 옴 (agent._ask_model).

[가격]
100만 토큰당 달러. 2026-11-17에 OpenAI 모델 페이지(developers.openai.com/api/docs/models/…)에서 확인한 값.
가격이 바뀌면 코드 대신 .env 에  PRICE_GPT_5_4_MINI=0.75,0.075,4.5  처럼 (입력,캐시 입력,출력) 넣으면 됨.
표에 없는 모델은 비용 대신 토큰만 셈 (모르는 가격을 지어내지 않음).

[어디에 쌓나]
- SESSION: 앱을 켠 뒤 합계 (메모리). 화면은 '새 대화' 누를 때 이 값을 기억해 뒀다가 빼서 '이번 대화' 비용을 보여줌
- data/usage.json: 날짜별·모델별 합계 (앱을 껐다 켜도 '오늘'·'이번 달' 비용이 남게)
"""

import os
import threading
from datetime import datetime

from core.paths import DATA_DIR
from core.storage import read_json, update_json

USAGE_FILE = DATA_DIR / "usage.json"
KEEP_DAYS = 120  # 오래된 날짜 기록은 버림 (부트캠프 기간 정도)

# 모델 이름 → (입력, 캐시 입력, 출력) $/1M 토큰
PRICES = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
}

SESSION = {"input": 0, "cached": 0, "output": 0, "cost": 0.0, "calls": 0}
_lock = threading.Lock()  # 채팅 스레드와 정리 카드 스레드가 동시에 더할 수 있어서


def price_of(model: str) -> tuple[float, float, float] | None:
    """.env 의 PRICE_모델이름(점·대시는 _) 이 있으면 그걸, 없으면 표. 날짜 붙은 이름(gpt-5.4-mini-2026…)도 찾음."""
    env = os.getenv("PRICE_" + model.upper().replace(".", "_").replace("-", "_"))
    if env:
        try:
            a, b, c = (float(x) for x in env.split(","))
            return a, b, c
        except ValueError:
            pass  # 형식이 틀리면 표 값으로
    if model in PRICES:
        return PRICES[model]
    # 가장 긴 이름부터 비교해야 'gpt-5.4-mini-…' 가 'gpt-5.4' 로 잘못 잡히지 않음
    for name in sorted(PRICES, key=len, reverse=True):
        if model.startswith(name + "-"):
            return PRICES[name]
    return None


def cost_of(model: str, input_tokens: int, cached: int, output: int) -> float | None:
    """캐시된 입력은 싼 값으로 따로 계산 (input_tokens 안에 cached 가 포함돼 있음)."""
    p = price_of(model)
    if p is None:
        return None
    return ((input_tokens - cached) * p[0] + cached * p[1] + output * p[2]) / 1_000_000


def record(model: str, usage) -> float:
    """응답 하나의 usage 를 더함. usage 는 OpenAI 객체(속성) 또는 dict 둘 다 받음. 이번 비용을 돌려줌."""
    if usage is None:
        return 0.0
    get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
    inp = get(usage, "prompt_tokens") or 0
    out = get(usage, "completion_tokens") or 0
    details = get(usage, "prompt_tokens_details")
    cached = (get(details, "cached_tokens") or 0) if details else 0
    cost = cost_of(model, inp, cached, out)
    with _lock:
        SESSION["input"] += inp
        SESSION["cached"] += cached
        SESSION["output"] += out
        SESSION["cost"] += cost or 0.0
        SESSION["calls"] += 1

    day = datetime.now().strftime("%Y-%m-%d")

    def add(data: dict) -> dict:
        m = data.setdefault(day, {}).setdefault(model, {"input": 0, "cached": 0, "output": 0, "cost": 0.0, "calls": 0})
        m["input"] += inp
        m["cached"] += cached
        m["output"] += out
        m["cost"] += cost or 0.0
        m["calls"] += 1
        return {d: data[d] for d in sorted(data)[-KEEP_DAYS:]}

    try:
        update_json(USAGE_FILE, {}, add, indent=1)
    except OSError:
        pass  # 기록 실패로 대화가 멈추면 안 됨
    return cost or 0.0


def totals(prefix: str) -> dict:
    """날짜가 prefix 로 시작하는 기록 합계. prefix = '2026-11-17'(하루) 또는 '2026-11'(한 달)."""
    out = {"input": 0, "cached": 0, "output": 0, "cost": 0.0, "calls": 0, "by_model": {}}
    for day, models in read_json(USAGE_FILE, {}).items():
        if not day.startswith(prefix):
            continue
        for model, m in models.items():
            for k in ("input", "cached", "output", "cost", "calls"):
                out[k] += m[k]
            out["by_model"][model] = out["by_model"].get(model, 0.0) + m["cost"]
    return out


def fmt_cost(dollars: float) -> str:
    """$0.0042 처럼 작은 값도 보이게. 1센트 넘으면 소수 둘째 자리까지."""
    return f"${dollars:.2f}" if dollars >= 0.01 else f"${dollars:.4f}"

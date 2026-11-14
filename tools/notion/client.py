"""Notion API 클라이언트: 요청 보내기·재시도·오류 메시지, DB 속성 이름.

[DB 구조] (Notion에서 확인한 실제 속성 이름)
  강의명(제목) / 유형(select: 강의, 실습) / 복습(checkbox) / 작성일시(created_time)
  날짜(date) / 과목(select) / 분류(select = DB 탭)

[설정] .env 에
  NOTION_TOKEN=ntn_...            ← Notion integration 토큰
  NOTION_DATA_SOURCE_ID=...       ← DB의 data source ID
"""

import json
import os
import time
import urllib.error
import urllib.request

NOTION_VERSION = "2025-09-03"  # 공식 SDK(notion-client) 최신판과 같은 API 버전
API = "https://api.notion.com/v1"

TITLE_PROP, TYPE_PROP, REVIEW_PROP = "강의명", "유형", "복습"
DATE_PROP = "날짜"  # 강의 날짜. '작성일시'(created_time)는 API로 못 바꿔서 따로 추가한 속성
TOPIC_PROP = "과목"  # 커리큘럼 기준 과목 (Python, 딥러닝, 프롬프트 엔지니어링 …)
CATEGORY_PROP = "분류"  # 페이지 종류 = DB 탭 (일차 노트 / 회고 / 프로젝트 / 면접 / 기타)


class NotionError(Exception):
    pass


# ================================================================ HTTP
def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Notion API 호출. 실패하면 사람이 이해할 수 있는 메시지로 NotionError를 던짐."""
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise NotionError("NOTION_TOKEN 이 설정되지 않았음. README의 'Notion 연결' 순서대로 토큰을 넣어야 함.")

    base = os.getenv("NOTION_BASE_URL", API)  # 테스트할 때 가짜 서버로 바꾸려고 둔 옵션
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 429 = 요청이 너무 많음. Notion은 초당 약 3회 제한이라 잠깐 쉬었다 재시도
            if e.code == 429 and attempt < 5:
                time.sleep(float(e.headers.get("Retry-After", "1")))
                continue
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise NotionError("Notion 토큰이 올바르지 않음 (401).") from e
            if e.code == 404:
                raise NotionError(
                    "Notion에서 DB/페이지를 못 찾음 (404). DB 페이지 오른쪽 위 ••• → '연결'에서 "
                    "만든 integration을 추가했는지 확인해야 함."
                ) from e
            raise NotionError(f"Notion API 오류 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise NotionError(f"Notion 서버에 연결 못 함: {e.reason}") from e
    raise NotionError("Notion 요청이 계속 거절됨 (429). 잠시 후 다시 시도해야 함.")


def _data_source_id() -> str:
    ds = os.getenv("NOTION_DATA_SOURCE_ID")
    if not ds:
        raise NotionError("NOTION_DATA_SOURCE_ID 가 설정되지 않았음.")
    return ds

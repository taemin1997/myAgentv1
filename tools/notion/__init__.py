"""Notion 공부 비서 패키지. 바깥(에이전트·화면)에서 쓰는 것만 여기서 꺼내 줌.

  client.py   요청·재시도·오류 메시지, DB 속성 이름
  blocks.py   Notion 블록 ↔ 텍스트/마크다운, 블록 끼워 넣기
  cache.py    로컬 캐시(data/notion_cache.json) 동기화, 백그라운드 동기화
  history.py  수정 기록·되돌리기 (undo_last_update)
  tools.py    모델이 부르는 도구들 + 노트 생성·제목 제안
"""

from .cache import SYNC_STATE, start_background_sync
from .client import NotionError
from .history import begin_batch
from .tools import NOTION_TOOLS, create_note, suggest_titles

__all__ = ["SYNC_STATE", "start_background_sync", "NotionError", "begin_batch",
           "NOTION_TOOLS", "create_note", "suggest_titles"]

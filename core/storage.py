"""data/ 의 JSON 파일을 안전하게 읽고 쓰기.

[왜 따로 만들었나]
- 예전엔 path.write_text 로 바로 덮어썼음. 이건 '파일 비우기 → 새 내용 쓰기' 순서라서
  1) 쓰는 도중에 다른 스레드가 읽으면 반쯤 쓴 JSON 을 읽고 JSONDecodeError (노션 캐시는 수 MB라 쓰는 데 시간이 걸림)
  2) 쓰는 도중에 앱이 꺼지면 파일이 깨진 채로 남아서, 다음 실행부터 검색할 때마다 오류
  → 임시 파일에 다 쓴 다음 os.replace 로 한 번에 바꿔치기함 (원자적 교체: 읽는 쪽은 옛 파일 아니면 새 파일만 봄)
- 백그라운드 동기화 스레드, 채팅 도구 스레드, 웹 화면의 정리 카드 스레드가 같은 파일을 만질 수 있음
  → 파일마다 잠금(lock)을 두고, '읽고 → 고치고 → 쓰기'를 한 번에 하는 update_json 을 씀
- 윈도우는 다른 스레드가 파일을 열어 둔 동안 os.replace 가 PermissionError 로 실패함
  → 읽기도 같은 잠금 안에서 하고, 다른 프로그램(백신·동기화 앱)이 잠깐 잡은 경우를 위해 몇 번 다시 시도
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: Path) -> threading.RLock:
    """파일 경로마다 잠금 하나. RLock 이라 같은 스레드가 안에서 또 잡아도 멈추지 않음 (update_json 안의 read/write)."""
    key = str(Path(path).resolve()).lower()
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def read_json(path: Path, default: Any) -> Any:
    """파일이 없으면 default. 깨져 있으면 .broken 으로 옮겨 두고 default (앱이 계속 죽지 않게, 원본은 남김).
    default 는 매번 새로 만들어서 넘길 것 (같은 dict 를 여러 번 돌려주면 서로 고쳐짐)."""
    path = Path(path)
    with lock_for(path):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            broken = path.with_name(path.name + ".broken")
            _replace(path, broken)
            return default


def write_json(path: Path, data: Any, indent: int | None = None) -> None:
    """임시 파일에 다 쓰고 나서 바꿔치기. 쓰다가 꺼져도 원래 파일은 멀쩡함."""
    path = Path(path)
    with lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        _replace(tmp, path)


def update_json(path: Path, default: Any, fn: Callable[[Any], Any], indent: int | None = None) -> Any:
    """읽고 → fn 으로 고치고 → 쓰기를 잠금 하나로 묶음 (중간에 다른 스레드가 끼어들어 수정이 사라지지 않게).
    fn 은 고친 값을 돌려줘야 함. 돌려준 값을 그대로 돌려줌."""
    path = Path(path)
    with lock_for(path):
        data = fn(read_json(path, default))
        write_json(path, data, indent)
        return data


def _replace(src: Path, dst: Path, tries: int = 10) -> None:
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))  # 다른 프로그램이 잠깐 잡고 있는 경우. 조금씩 더 기다림

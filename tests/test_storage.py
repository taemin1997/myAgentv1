"""data/ 파일 저장: 깨진 파일 복구, 동시에 읽고 쓰기, 동기화 중 복습 체크가 안 사라지는지."""

import threading

import tools.notion.cache as ncache
import tools.notion.tools as ntools
from core.storage import read_json, update_json, write_json


def test_roundtrip_and_missing(tmp_path):
    p = tmp_path / "a.json"
    assert read_json(p, []) == []
    write_json(p, {"한글": [1, 2]})
    assert read_json(p, None) == {"한글": [1, 2]}
    assert not list(tmp_path.glob("*.tmp"))  # 임시 파일이 안 남음


def test_broken_file_is_set_aside(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"반쯤 쓰다 꺼진', encoding="utf-8")
    assert read_json(p, {"default": True}) == {"default": True}
    assert (tmp_path / "a.json.broken").exists()  # 원본은 지우지 않고 옆에 남김


def test_reader_never_sees_half_written_file(tmp_path):
    """큰 파일을 계속 덮어쓰는 동안 다른 스레드가 읽어도 JSON 오류가 안 나야 함 (예전 write_text 방식의 문제)."""
    p = tmp_path / "cache.json"
    big = {"pages": {str(i): {"text": "가" * 2000} for i in range(200)}}
    write_json(p, big)
    errors, stop = [], threading.Event()

    def writer():
        for i in range(30):
            big["n"] = i
            write_json(p, big, indent=1)
        stop.set()

    def reader():
        while not stop.is_set():
            try:
                assert len(read_json(p, None)["pages"]) == 200
            except Exception as e:  # noqa: BLE001 - 어떤 오류든 기록
                errors.append(e)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_update_json_does_not_lose_updates(tmp_path):
    p = tmp_path / "count.json"

    def bump():
        for _ in range(50):
            update_json(p, {"n": 0}, lambda d: {"n": d["n"] + 1})

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert read_json(p, None) == {"n": 400}


def test_sync_save_keeps_newer_review_check(cache_with, notion):
    """동기화가 메모리에 옛 캐시를 들고 있는 동안 복습 체크를 하면, 동기화의 다음 저장이 그걸 덮어쓰면 안 됨."""
    cache_with({"p1": {"title": "45일차", "text": "옛 본문"}})
    syncing = ncache._load_cache()  # 동기화 스레드가 들고 있는 사본

    assert "체크함" in ntools.mark_note_reviewed("45일차")

    syncing["pages"]["p1"]["text"] = "동기화가 새로 받은 본문"  # 그 사이 동기화가 본문을 받음
    ncache._save_cache(syncing)

    saved = ncache._load_cache()["pages"]["p1"]
    assert saved["reviewed"] is True  # 복습 체크가 살아 있음
    assert saved["text"] == "동기화가 새로 받은 본문"  # 새로 받은 본문도 버리지 않음

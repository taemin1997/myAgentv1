"""파일 위치 기준점.

exe로 실행하면 __file__ 이 임시 폴더를 가리키므로, .env 와 data/ 를
exe 파일이 있는 폴더에 두기 위해 기준 폴더를 따로 계산한다.
"""

import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller로 만든 exe
        return Path(sys.executable).parent
    # 이 파일은 core/ 안에 있으니 한 단계 위(저장소 맨 위)가 기준. .env·data/·prompts/ 가 거기 있음
    return Path(__file__).resolve().parent.parent


APP_DIR = app_dir()
ENV_FILE = APP_DIR / ".env"
DATA_DIR = APP_DIR / "data"


def save_env(name: str, value: str) -> None:
    """.env 의 name= 줄을 새 값으로 바꾸거나, 없으면 추가한다. 실행 중인 프로그램에도 바로 반영.
    (예전 화면 ctk_app 과 웹 화면 web_app 이 같이 쓰려고 여기로 옮김)"""
    import os

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if not l.strip().startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value

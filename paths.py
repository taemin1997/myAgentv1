"""파일 위치 기준점.

exe로 실행하면 __file__ 이 임시 폴더를 가리키므로, .env 와 data/ 를
exe 파일이 있는 폴더에 두기 위해 기준 폴더를 따로 계산한다.
"""

import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller로 만든 exe
        return Path(sys.executable).parent
    return Path(__file__).parent


APP_DIR = app_dir()
ENV_FILE = APP_DIR / ".env"
DATA_DIR = APP_DIR / "data"

"""개인 비서 - 창(GUI) 버전 실행 파일.

실행:  uv run app.py          웹 화면 (ui/web_app.py, pywebview)
       uv run app.py --ctk    예전 CustomTkinter 화면 (ui/ctk_app.py)
       (또는 scripts/개인비서.bat 더블클릭)
.env 에 UI=ctk 를 넣어도 예전 화면으로 뜸. 웹 화면이 안정되면 ctk 화면은 지울 예정.
여기는 진입점만 둠 (exe 빌드도 이 파일에서 시작).
"""

import os
import sys

from dotenv import load_dotenv

from core.paths import ENV_FILE

load_dotenv(ENV_FILE)


def main() -> None:
    if "--ctk" in sys.argv or os.getenv("UI", "").lower() == "ctk":
        from ui.ctk_app import main as run
    else:
        try:
            from ui.web_app import main as run
        except ImportError:  # pywebview(또는 WebView2 연결용 pythonnet)가 없으면 예전 화면으로
            from ui.ctk_app import main as run
    run()


if __name__ == "__main__":
    main()

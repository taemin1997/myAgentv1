"""개인 비서 - 창(GUI) 버전 실행 파일.

실행:  uv run app.py   (또는 scripts/개인비서.bat 더블클릭)
화면 코드는 ui/ctk_app.py. 여기는 진입점만 둠 (exe 빌드도 이 파일에서 시작).
"""

from ui.ctk_app import main

if __name__ == "__main__":
    main()

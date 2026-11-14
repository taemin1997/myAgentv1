"""개인 비서 에이전트 - 터미널 버전. (창으로 된 버전은 app.py)

실행:  uv run main.py
종료:  exit / quit / 종료 입력, 또는 Ctrl+C
"""

import os
import sys

import openai

from agent import MODEL, friendly_error, new_conversation, run_turn


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 가 없습니다. .env.example 을 .env 로 복사하고 키를 넣어 주세요.")
        sys.exit(1)

    client = openai.OpenAI()
    messages = new_conversation()

    print(f"🤖 개인 비서 ({MODEL}) - 종료하려면 exit\n")
    while True:
        try:
            user_input = input("나 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "종료"}:
            break

        checkpoint = len(messages)  # 실패하면 이 시점으로 되돌림
        messages.append({"role": "user", "content": user_input})
        try:
            answer = run_turn(client, messages, on_tool=lambda n, a: print(f"  🔧 {n}({a})"))
        except openai.OpenAIError as e:
            print(friendly_error(e) + "\n")
            del messages[checkpoint:]
            if isinstance(e, openai.AuthenticationError):
                break
            continue

        print(f"비서 > {answer}\n")


if __name__ == "__main__":
    main()

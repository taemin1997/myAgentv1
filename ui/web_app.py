"""개인 비서 - 웹 화면 버전 (pywebview + HTML/CSS/JS).

실행:  uv run app.py   (예전 CustomTkinter 화면은  uv run app.py --ctk)
화면 파일은 ui/web/ (index.html · app.css · app.js · markdown.js).

구조:
  - 파이썬 → 화면: 작업 스레드는 화면을 직접 건드리지 않고 events 큐에 넣기만 함.
    화면(JS)이 0.1초마다 api.poll() 로 쌓인 걸 한꺼번에 가져가서 그림.
    → 예전 ctk 화면의 '0.1초마다 한 번 다시 그리기' 원칙이 그대로 유지되고,
      evaluate_js 를 스레드마다 부르는 것보다 순서가 꼬일 일이 없음.
  - 화면 → 파이썬: JS 가 window.pywebview.api.이름(...) 으로 아래 Api 의 메서드를 부름.
    pywebview 가 호출마다 별도 스레드에서 실행해 주니까 오래 걸리는 일도 화면이 안 멈춤.
  - 끌어다 놓기: 브라우저 drop 이벤트에는 보안상 실제 경로가 없음. pywebview 의 DOM 이벤트로
    파이썬 쪽에서 받으면 file['pywebviewFullPath'] 에 실제 경로가 들어 있음 (_on_drop).

주의: Api 의 '_' 로 시작하지 않는 속성·메서드는 전부 JS 에 노출됨.
상태 값(대화 기록, openai 클라이언트 등)은 꼭 '_' 를 붙여서 숨길 것.
"""

import itertools
import os
import queue
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import openai
import webview
from webview.dom import DOMEventHandler

import tools.notion as notion_tools
from core import usage
from core.agent import HEAVY_MODEL, MODEL, friendly_error, new_conversation, run_turn, summarize_pdfs
from core.paths import save_env
from study import templates
from study.attachments import FILE_TYPES, build_user_content, drop_old_pdf_bodies, load_file

# exe(onefile)에서는 ui/web 이 임시 폴더(_MEIPASS/ui/web)에 풀리는데, __file__ 도 그쪽을 가리켜서 그대로 쓰면 됨
WEB_DIR = Path(__file__).resolve().parent / "web"

GREETING = "안녕하세요! 공부 노트 검색·복습 퀴즈·노션 정리, 할 일, 메모, 날씨 등 무엇이든 말씀하세요."


def _dialog_file_types() -> tuple[str, ...]:
    """study.attachments.FILE_TYPES(tkinter 형식) → pywebview 형식 '설명 (*.pdf;*.docx)'.
    pywebview 는 설명에 글자·공백만 허용해서 '/' 같은 기호는 뺌."""
    out = []
    for desc, patterns in FILE_TYPES:
        clean = "".join(ch for ch in desc if ch.isalnum() or ch == " ").strip() or "파일"
        out.append(f"{clean} ({';'.join(patterns.split())})")
    return tuple(out)


class Api:
    """JS 에서 부르는 함수 모음. 돌려주는 값은 JSON 으로 바뀌어서 JS 로 감."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._client: openai.OpenAI | None = openai.OpenAI() if os.getenv("OPENAI_API_KEY") else None
        self._messages = new_conversation()
        self._events: queue.Queue = queue.Queue()  # 작업 스레드 → 화면 전달용
        self._busy = False
        self._attachments: list = []  # 다음 메시지에 같이 보낼 파일들 (PDF·문서·이미지)
        self._cards: dict[int, dict] = {}  # 정리 카드 id → {attachments, template}
        self._card_ids = itertools.count(1)
        self._lock = threading.Lock()  # 첨부 목록을 드롭(파이썬 스레드)과 버튼(JS 호출)이 같이 만져서
        self._conv_base = usage.SESSION["cost"]  # '이번 대화' 비용 = 지금 합계 - 대화 시작 때 합계
        self._usage_key = None
        self._usage_cache: dict = {}

    # ------------------------------------------------------------ 시작·상태
    def init(self) -> dict:
        """화면이 처음 뜰 때 한 번. 모델 이름·인사말·템플릿 목록·키 유무."""
        return {
            "model": MODEL,
            "heavy_model": HEAVY_MODEL,
            "greeting": GREETING,
            "templates": list(templates.card_template_labels()),
            "has_key": self._client is not None,
        }

    def poll(self) -> dict:
        """0.1초마다 JS 가 부름: 쌓인 이벤트 전부 + 상태줄 글."""
        events = []
        try:
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return {"events": events, "busy": self._busy, "status": self._status_text(), "usage": self._usage()}

    def _usage(self) -> dict:
        """상단 비용 표시. data/usage.json 을 0.1초마다 읽으면 낭비라서, 모델 호출 수가 바뀌었을 때만 다시 계산."""
        key = (usage.SESSION["calls"], self._conv_base, datetime.now().strftime("%Y-%m-%d"))
        if key != self._usage_key:
            today, month = usage.totals(key[2]), usage.totals(key[2][:7])
            conv = usage.SESSION["cost"] - self._conv_base
            by_model = ", ".join(f"{m} {usage.fmt_cost(c)}" for m, c in today["by_model"].items()) or "없음"
            self._usage_cache = {
                "label": f"이번 대화 {usage.fmt_cost(conv)} · 오늘 {usage.fmt_cost(today['cost'])}",
                "tip": (f"오늘: 입력 {today['input']:,} (캐시 {today['cached']:,}) / 출력 {today['output']:,} 토큰, "
                        f"호출 {today['calls']}번\n오늘 모델별: {by_model}\n이번 달: {usage.fmt_cost(month['cost'])}\n"
                        "가격은 core/usage.py 표 기준 추정치 (실제 청구는 OpenAI 대시보드)"),
            }
            self._usage_key = key
        return self._usage_cache

    def _status_text(self) -> str:
        """창 아래 상태줄: '생각 중...' 과 노션 동기화 진행률을 같이 보여줌."""
        parts = []
        if self._busy:
            parts.append("생각 중...")
        s = notion_tools.SYNC_STATE
        if s["running"]:
            parts.append(f"📚 노션 노트 동기화 중 {s['done']}/{s['total']}" if s["total"] else "📚 노션 노트 확인 중...")
        return "   ·   ".join(parts)

    def _emit(self, kind: str, **data) -> None:
        self._events.put({"type": kind, **data})

    # ------------------------------------------------------------ 키·토큰
    def save_api_key(self, key: str) -> dict:
        """값은 .env 에만 쓰고 어디에도 출력하지 않음."""
        key = (key or "").strip()
        if not key:
            return {"ok": False}
        save_env("OPENAI_API_KEY", key)
        self._client = openai.OpenAI()
        return {"ok": True, "message": "API 키를 저장했어요."}

    def save_notion_token(self, token: str) -> dict:
        token = (token or "").strip()
        if not token:
            return {"ok": False}
        save_env("NOTION_TOKEN", token)
        notion_tools.start_background_sync()
        return {"ok": True, "message": "Notion 토큰을 저장했어요. 노트를 내려받는 중이에요 (처음엔 몇 분 걸려요)."}

    def open_url(self, url: str) -> None:
        """말풍선 안 링크는 앱 창이 아니라 기본 브라우저로 엶 (창 안에서 열리면 채팅 화면이 사라짐)."""
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)

    # ------------------------------------------------------------ 대화
    def new_chat(self) -> dict:
        if self._busy:
            return {"ok": False}
        self._messages = new_conversation()
        self._conv_base = usage.SESSION["cost"]
        return {"ok": True, "message": "새 대화를 시작합니다. 무엇을 도와드릴까요?"}

    def send(self, text: str) -> dict:
        """메시지 보내기. 답은 poll 로 흘러감. 화면에 보여줄 내 말풍선 글을 돌려줌."""
        text = (text or "").strip()
        with self._lock:
            if self._busy or not (text or self._attachments):
                return {"ok": False}
            if self._client is None:
                return {"ok": False, "need_key": True}
            if not text:  # 첨부만 하고 바로 보낸 경우
                text = "첨부한 자료 내용을 공부 노트로 정리해줘."
            # 화면엔 짧게, 모델에겐 본문(이미지는 그림 그대로)까지
            shown = text + "".join(f"\n{a.summary}" for a in self._attachments)
            content = build_user_content(text, self._attachments)
            has_files = bool(self._attachments)
            self._attachments.clear()
            self._busy = True
        threading.Thread(target=self._worker, args=(content, has_files), daemon=True).start()
        return {"ok": True, "shown": shown, "attachments": []}

    def _worker(self, content, has_files: bool) -> None:
        """작업 스레드: 화면을 직접 만지지 않고 events 큐에만 넣는다."""
        if has_files:
            drop_old_pdf_bodies(self._messages)  # 예전 첨부 본문·이미지는 빼서 비용이 안 쌓이게
        checkpoint = len(self._messages)
        self._messages.append({"role": "user", "content": content})
        try:
            answer = run_turn(
                self._client, self._messages,
                on_tool=self._on_tool,
                # 글 조각은 큐에 넣기만 → JS 가 0.1초마다 가져가서 말풍선에 이어 붙임
                on_text=lambda kind, text: self._emit("s_" + kind, text=text),
            )
            self._emit("final", text=answer)
        except Exception as e:
            del self._messages[checkpoint:]  # 실패한 질문은 기록에서 제거
            self._emit("error", text=friendly_error(e))
        finally:
            self._busy = False
            self._emit("done")

    def _on_tool(self, name: str, args: str) -> None:
        if name.startswith("⚡"):  # agent.run_turn 이 상위 모델로 바꿀 때 보내는 표시 (args = 모델 이름)
            self._emit("heavy", text=name, model=args)
        elif name.startswith("🗜"):  # agent.compact_history 가 오래된 대화를 요약했을 때 (args = 줄어든 토큰)
            self._emit("notice", text=f"{name} ({args})")
        else:
            self._emit("tool", name=name, args=args)

    # ------------------------------------------------------------ 첨부
    def pick_files(self) -> dict:
        """📎 버튼: 파일 선택 창 (여러 개 가능)."""
        if self._busy or self._window is None:
            return self.attachments()
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                                file_types=_dialog_file_types())
        return self.add_files(list(paths or []))

    def add_files(self, paths: list) -> dict:
        """📎 버튼과 끌어다 놓기가 같이 씀. 못 읽는 파일은 오류만 보여주고 나머지는 계속 첨부."""
        if self._busy:
            return self.attachments()
        for path in paths:
            p = Path(path)
            if p.is_dir():  # 폴더를 끌어오면 안에 있는 파일 전부 (지원 안 하는 건 load_file 이 오류로 알려 줌)
                self.add_files(sorted(str(f) for f in p.iterdir() if f.is_file()))
                continue
            try:
                att = load_file(p)
                with self._lock:
                    self._attachments.append(att)
            except Exception as e:
                self._emit("error", text=f"첨부 못 함: {p.name} ({e})")
        return self.attachments()

    def attachments(self) -> dict:
        """첨부 표시줄에 그릴 것. 채팅으로 새 템플릿을 저장했을 수 있으니 템플릿 목록도 매번 새로."""
        with self._lock:
            items = [{"summary": a.summary, "warning": a.warning, "kind": a.kind} for a in self._attachments]
        return {"items": items, "templates": list(templates.card_template_labels())}

    def clear_attachments(self) -> dict:
        with self._lock:
            self._attachments.clear()
        return self.attachments()

    def _on_drop(self, event: dict) -> None:
        """파이썬 쪽 drop 이벤트 (pywebview 가 별도 스레드에서 부름). 실제 경로로 첨부하고 화면에 알림."""
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            self._emit("attachments", **self.add_files(paths))

    # ------------------------------------------------------------ 정리 카드
    def make_note_cards(self, template_label: str, merge: bool) -> dict:
        """첨부한 파일마다(또는 합쳐서 하나) 정리 카드를 만든다. 카드가 각자 모델에 따로 요청함.
        채팅 기록과 상관없는 1회성 호출이라 PDF 본문이 대화 기록에 안 쌓임 (agent.summarize_pdfs)."""
        if self._client is None:
            return {"ok": False, "need_key": True}
        with self._lock:
            if not self._attachments:
                return {"ok": False}
            atts = sorted(self._attachments, key=lambda a: a.name)  # 파일 이름순 = 날짜순이라고 가정
            self._attachments.clear()
        groups = [atts] if merge else [[a] for a in atts]
        # 고른 순간 파일을 다시 읽음 → 앱을 켜 둔 채 템플릿 파일을 고쳐도 바로 반영됨
        name = templates.card_template_labels().get(template_label, "강의노트")
        template = templates.get_template(name) or templates.get_template("강의노트")
        try:
            suggestions = notion_tools.suggest_titles(template.title_rule, len(groups))
        except Exception:
            suggestions = [("", "")] * len(groups)

        cards = []
        for group, (title, date) in zip(groups, suggestions):
            cid = next(self._card_ids)
            self._cards[cid] = {"attachments": group, "template": template}
            icon = templates.TAB_ICON.get(template.category, "📄")
            cards.append({
                "id": cid,
                "header": f"{', '.join(a.name for a in group)}  →  {icon} {template.category} 탭 ({template.name})",
                "title": title, "date": date,
                "note_type": template.note_type or "없음",
                "wide_title": "주차" in template.title_rule,  # '16주차 회고'가 안 잘리게
            })
            threading.Thread(target=self._summarize_card, args=(cid, title), daemon=True).start()
        return {"ok": True, "cards": cards, "attachments": self.attachments()}

    def _summarize_card(self, cid: int, title: str) -> None:
        card = self._cards[cid]
        try:
            md = summarize_pdfs(self._client, card["attachments"], title, card["template"].name)
            self._emit("card_draft", id=cid, text=md)
        except Exception as e:
            self._emit("card_draft", id=cid, text="", error=friendly_error(e))

    def save_card(self, cid: int, title: str, content: str, note_type: str, date: str) -> dict:
        """카드의 'Notion에 저장'. 저장은 오래 걸릴 수 있어서 스레드로 돌리고 결과는 poll 로."""
        card = self._cards.get(int(cid))
        title, content = (title or "").strip(), (content or "").strip()
        if card is None or not title or not content:
            return {"ok": False, "message": "제목과 내용이 필요해요"}

        def run():
            t = card["template"]
            try:
                r = notion_tools.create_note(title, content, note_type, (date or "").strip(), t.category, t.topic)
                self._emit("card_saved", id=int(cid), url=r.get("url", ""))
            except Exception as e:
                self._emit("card_failed", id=int(cid), message=str(e)[:120])

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}


def main() -> None:
    api = Api()
    window = webview.create_window(
        "개인 비서", url=str(WEB_DIR / "index.html"), js_api=api,
        width=600, height=780, min_size=(380, 480), background_color="#f6f7fb",
    )
    api._window = window

    def on_loaded():
        # drop 은 문서 전체에서 받음. prevent_default: 브라우저가 파일을 열어 버리는 기본 동작 막기
        window.dom.document.events.drop += DOMEventHandler(api._on_drop, prevent_default=True)
        # 켜자마자 노션 노트를 백그라운드로 최신화 (바뀐 것만 받으니 보통 금방 끝남)
        notion_tools.start_background_sync()

    window.events.loaded += on_loaded
    # WEB_DEBUG=1 이면 F12 개발자 도구 사용 가능 (화면 고칠 때)
    webview.start(debug=os.getenv("WEB_DEBUG") == "1")


if __name__ == "__main__":
    main()

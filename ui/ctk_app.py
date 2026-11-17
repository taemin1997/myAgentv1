"""개인 비서 에이전트 - 창(GUI) 버전.

실행:  uv run app.py   (또는 '개인비서.bat' 더블클릭)

구조:
  - 화면(tkinter)은 메인 스레드에서만 건드려야 한다.
  - API 호출은 오래 걸리므로 별도 스레드에서 실행하고,
    결과는 queue에 넣어 두면 메인 스레드가 0.1초마다 꺼내서 화면에 그린다.
"""

import os
import queue
import re
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import openai
from tkinterdnd2 import DND_FILES, TkinterDnD

import tools.notion as notion_tools
from core.agent import MODEL, friendly_error, new_conversation, run_turn
from core.paths import save_env
from study import templates
from study.attachments import FILE_TYPES, build_user_content, drop_old_pdf_bodies, load_file
from ui.note_cards import NoteCard

FONT_FAMILY = "Malgun Gothic"

# 말풍선 색 (라이트, 다크)
USER_BG = ("#3b82f6", "#2563eb")
BOT_BG = ("#e5e7eb", "#374151")
USER_FG = ("white", "white")
BOT_FG = ("#111827", "#f3f4f6")
TOOL_FG = ("#6b7280", "#9ca3af")


def pretty_markdown(text: str) -> str:
    """말풍선(일반 라벨)은 마크다운을 못 그려서 기호가 그대로 보임 → 읽기 좋게 기호만 정리.
    모델이 만든 원문(대화 기록, 노션 저장본)은 건드리지 않음."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line.strip())
        if m:  # 제목 → 앞에 표시 기호
            line = ("■ " if len(m.group(1)) <= 2 else "▸ ") + m.group(2)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)  # **굵게** → 굵게
        line = re.sub(r"^(\s*)[-*] ", r"\1• ", line)  # - 목록 → • 목록
        out.append(line)
    return "\n".join(out)


class AssistantApp(ctk.CTk, TkinterDnD.DnDWrapper):
    """CustomTkinter 창 + 파일 끌어다 놓기(tkinterdnd2).
    tkinter 자체엔 드래그 앤 드롭이 없어서 tkdnd 확장을 창에 불러와야 함 (_require)."""

    def __init__(self) -> None:
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.title("개인 비서")
        self.geometry("520x720")
        self.minsize(380, 480)

        self.font = ctk.CTkFont(family=FONT_FAMILY, size=14)
        self.small_font = ctk.CTkFont(family=FONT_FAMILY, size=12)

        self.client: openai.OpenAI | None = None
        self.messages = new_conversation()
        self.events: queue.Queue = queue.Queue()  # 작업 스레드 → 화면 전달용
        self.busy = False
        self.attachments: list = []  # 다음 메시지에 같이 보낼 파일들 (PDF·문서·이미지)
        # 스트리밍 상태: 지금 글자가 채워지는 중인 말풍선과 그 안의 글
        self.stream_label = None
        self.stream_buf = ""
        self.stream_dirty = False   # 새 조각이 와서 화면을 다시 그려야 하는지
        self.last_streamed = None   # 마지막으로 다 흘려보낸 글 (최종 답과 같으면 말풍선을 또 만들지 않음)

        self._build_ui()
        self._enable_drop()
        self.after(100, self._poll_events)
        self.after(200, self._ensure_api_key)
        # 켜자마자 노션 노트를 백그라운드로 최신화 (바뀐 것만 받으니 보통 금방 끝남)
        self.after(500, notion_tools.start_background_sync)

    # ------------------------------------------------------------ 화면 구성
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 상단 바
        top = ctk.CTkFrame(self, corner_radius=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=f"🤖 개인 비서  ·  {MODEL}", font=self.font).grid(
            row=0, column=0, padx=14, pady=10, sticky="w"
        )
        ctk.CTkButton(top, text="새 대화", width=70, font=self.small_font, command=self.new_chat).grid(
            row=0, column=1, padx=(0, 6), pady=10
        )
        ctk.CTkButton(top, text="Notion", width=60, font=self.small_font, fg_color="gray",
                      command=self._ask_notion_token).grid(row=0, column=2, padx=(0, 6), pady=10)
        ctk.CTkButton(top, text="API 키", width=60, font=self.small_font, fg_color="gray",
                      command=lambda: self._ask_api_key(force=True)).grid(row=0, column=3, padx=(0, 14), pady=10)

        # 대화 영역
        self.chat = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")
        self.chat.grid(row=1, column=0, sticky="nsew")
        self.chat.grid_columnconfigure(0, weight=1)
        self.chat_row = 0

        # 상태 표시 (생각 중...)
        self.status = ctk.CTkLabel(self, text="", font=self.small_font, text_color=TOOL_FG)
        self.status.grid(row=2, column=0, padx=14, sticky="w")

        # 입력창
        bottom = ctk.CTkFrame(self, corner_radius=0)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_columnconfigure(1, weight=1)

        # 첨부 파일 표시줄 (첨부가 있을 때만 보임)
        self.attach_bar = ctk.CTkFrame(bottom, fg_color="transparent")
        self.attach_label = ctk.CTkLabel(self.attach_bar, text="", font=self.small_font, text_color=TOOL_FG,
                                         justify="left", anchor="w")
        self.attach_label.pack(side="top", anchor="w")
        actions = ctk.CTkFrame(self.attach_bar, fg_color="transparent")
        actions.pack(side="top", anchor="w", pady=(4, 0))
        # 정리 방식 = prompts 폴더의 템플릿 (강의노트 / 회의록 / 주간회고 / 직접 추가한 것)
        # 보여주는 이름(📅 일차 노트 · 강의) → 템플릿 이름(강의노트). Notion 탭과 같은 이름·아이콘
        self.template_labels = templates.card_template_labels()
        self.template_menu = ctk.CTkOptionMenu(actions, values=list(self.template_labels),
                                               width=150, height=26, font=self.small_font,
                                               dropdown_font=self.small_font)
        self.template_menu.set(next(iter(self.template_labels)))
        self.template_menu.pack(side="left", padx=(0, 6))
        ctk.CTkButton(actions, text="📝 노트로 정리", width=100, height=26, font=self.small_font,
                      command=self.make_note_cards).pack(side="left")
        self.merge_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(actions, text="하루치로 합치기", variable=self.merge_var, font=self.small_font,
                        checkbox_width=18, checkbox_height=18).pack(side="left", padx=10)
        ctk.CTkButton(actions, text="✕ 첨부 취소", width=70, height=26, font=self.small_font,
                      fg_color="gray", command=self.clear_attachments).pack(side="left")

        self.attach_btn = ctk.CTkButton(bottom, text="📎", width=40, height=40, font=self.font,
                                        fg_color="gray", command=self.attach_files)
        self.attach_btn.grid(row=1, column=0, padx=(14, 6), pady=12)
        self.entry = ctk.CTkEntry(bottom, font=self.font, height=40, placeholder_text="메시지 입력 (Enter) · 파일은 창에 끌어다 놓기")
        self.entry.grid(row=1, column=1, padx=(0, 6), pady=12, sticky="ew")
        self.entry.bind("<Return>", lambda _e: self.send())
        self.send_btn = ctk.CTkButton(bottom, text="전송", width=70, height=40, font=self.font, command=self.send)
        self.send_btn.grid(row=1, column=2, padx=(0, 14), pady=12)

        self._add_bubble("안녕하세요! 공부 노트 검색·복습 퀴즈·노션 정리, 할 일, 메모, 날씨 등 무엇이든 말씀하세요.", "bot")
        self.entry.focus()

    def _add_bubble(self, text: str, who: str):
        """who: 'user' | 'bot' | 'tool' | 'error'"""
        if who == "bot":
            text = pretty_markdown(text)
        if who == "tool":
            widget = ctk.CTkLabel(self.chat, text=text, font=self.small_font, text_color=TOOL_FG)
            widget.grid(row=self.chat_row, column=0, padx=18, pady=1, sticky="w")
        else:
            is_user = who == "user"
            width = self.winfo_width()  # 창이 아직 안 그려졌으면 1이 나옴
            widget = ctk.CTkLabel(
                self.chat,
                text=text,
                font=self.font,
                wraplength=width - 160 if width > 400 else 360,
                justify="left",
                corner_radius=14,
                fg_color=USER_BG if is_user else (("#fee2e2", "#7f1d1d") if who == "error" else BOT_BG),
                text_color=USER_FG if is_user else BOT_FG,
            )
            widget.grid(row=self.chat_row, column=0, padx=14, pady=5, ipadx=10, ipady=6,
                        sticky="e" if is_user else "w")
        self.chat_row += 1
        self._scroll_bottom()
        return widget  # 스트리밍 때 이 말풍선 글자를 계속 바꾸려고 돌려줌

    def _scroll_bottom(self) -> None:
        self.after(50, lambda: self.chat._parent_canvas.yview_moveto(1.0))  # 맨 아래로 스크롤

    # ------------------------------------------------------------ API 키
    def _ensure_api_key(self) -> None:
        if os.getenv("OPENAI_API_KEY"):
            self.client = openai.OpenAI()
        else:
            self._ask_api_key()

    def _ask_api_key(self, force: bool = False) -> None:
        dialog = ctk.CTkInputDialog(title="OpenAI API 키", text="OpenAI API 키를 입력하세요.\n(.env 파일에 저장됩니다)")
        key = (dialog.get_input() or "").strip()
        if key:
            save_env("OPENAI_API_KEY", key)
            self.client = openai.OpenAI()
            self._add_bubble("API 키를 저장했어요.", "tool")
        elif not force:
            self._add_bubble("API 키가 없어요. 오른쪽 위 'API 키' 버튼으로 입력해 주세요.", "error")

    def _ask_notion_token(self) -> None:
        dialog = ctk.CTkInputDialog(
            title="Notion 연결",
            text="Notion integration 토큰을 입력하세요.\n(ntn_ 로 시작, .env 파일에 저장됩니다)",
        )
        token = (dialog.get_input() or "").strip()
        if token:
            save_env("NOTION_TOKEN", token)
            notion_tools.start_background_sync()
            self._add_bubble("Notion 토큰을 저장했어요. 노트를 내려받는 중이에요 (처음엔 몇 분 걸려요).", "tool")

    # ------------------------------------------------------------ 대화
    def new_chat(self) -> None:
        if self.busy:
            return
        self.messages = new_conversation()
        for w in self.chat.winfo_children():
            w.destroy()
        self.chat_row = 0
        self._add_bubble("새 대화를 시작합니다. 무엇을 도와드릴까요?", "bot")

    # ------------------------------------------------------------ 첨부
    def attach_files(self) -> None:
        paths = filedialog.askopenfilenames(title="첨부할 파일 선택 (여러 개 가능)", filetypes=FILE_TYPES)
        self.add_files(paths)

    def add_files(self, paths) -> None:
        """📎 버튼과 끌어다 놓기가 같이 씀. 못 읽는 파일은 오류만 보여주고 나머지는 계속 첨부."""
        if self.busy:
            return
        for path in paths:
            p = Path(path)
            if p.is_dir():  # 폴더를 끌어오면 안에 있는 지원 파일 전부
                self.add_files(sorted(str(f) for f in p.iterdir() if f.is_file()))
                continue
            try:
                self.attachments.append(load_file(p))
            except Exception as e:
                self._add_bubble(f"첨부 못 함: {p.name} ({e})", "error")
        self._refresh_attach_bar()

    def _enable_drop(self) -> None:
        """창 어디에 떨어뜨려도 받도록 창과 큰 영역들을 '놓기 대상'으로 등록.
        (tkdnd는 마우스 아래 위젯이 등록돼 있어야 받아서, 창만 등록하면 대화 영역 위에선 안 될 수 있음)"""
        targets = [self, self.chat, self.chat._parent_canvas, self.entry, self.status]
        for w in targets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass  # 일부 위젯이 등록을 못 해도 창 자체로 받으면 됨

    def _on_drop(self, event):
        # 경로에 공백이 있으면 {C:/my files/a.pdf} 처럼 중괄호로 묶여 옴 → splitlist 가 풀어 줌
        self.add_files(self.tk.splitlist(event.data))
        return event.action

    def make_note_cards(self) -> None:
        """첨부한 파일마다(또는 합쳐서 하나) 정리 카드를 만든다. 카드가 각자 모델에 따로 요청함."""
        if not self.attachments:
            return
        if self.client is None:
            self._ask_api_key()
            if self.client is None:
                return
        atts = sorted(self.attachments, key=lambda a: a.name)  # 파일 이름순 = 날짜순이라고 가정
        groups = [atts] if self.merge_var.get() else [[a] for a in atts]
        # 고른 순간 파일을 다시 읽음 → 앱을 켜 둔 채 템플릿 파일을 고쳐도 바로 반영됨
        name = self.template_labels.get(self.template_menu.get(), "강의노트")
        template = templates.get_template(name) or templates.get_template("강의노트")
        try:
            suggestions = notion_tools.suggest_titles(template.title_rule, len(groups))
        except Exception:
            suggestions = [("", "")] * len(groups)
        for group, (title, date) in zip(groups, suggestions):
            card = NoteCard(self.chat, attachments=group, title=title, date=date, client=self.client,
                            template=template,
                            run_on_ui=lambda fn: self.events.put(("call", fn)),
                            font=self.small_font, small_font=self.small_font)
            card.grid(row=self.chat_row, column=0, padx=14, pady=6, sticky="ew")
            self.chat_row += 1
        self._add_bubble(f"정리 카드 {len(groups)}개를 만들었어요. 제목·날짜를 확인하고 하나씩 저장하세요. "
                         "(날짜는 평일 기준 추정이라 휴일이 끼면 고쳐 주세요)", "tool")
        self.clear_attachments()

    def clear_attachments(self) -> None:
        self.attachments.clear()
        self._refresh_attach_bar()

    def _refresh_attach_bar(self) -> None:
        if not self.attachments:
            self.attach_bar.grid_forget()
            return
        # 채팅으로 새 템플릿을 저장했을 수 있으니 첨부할 때마다 목록 갱신
        self.template_labels = templates.card_template_labels()
        self.template_menu.configure(values=list(self.template_labels))
        if self.template_menu.get() not in self.template_labels:
            self.template_menu.set(next(iter(self.template_labels)))
        lines = [a.summary + (f" ⚠️ {a.warning}" if a.warning else "") for a in self.attachments]
        self.attach_label.configure(text="\n".join(lines))
        self.attach_bar.grid(row=0, column=0, columnspan=3, padx=14, pady=(8, 0), sticky="w")
        self.entry.focus()

    # ------------------------------------------------------------ 전송
    def send(self) -> None:
        text = self.entry.get().strip()
        if self.busy or not (text or self.attachments):
            return
        if self.client is None:
            self._ask_api_key()
            if self.client is None:
                return
        if not text:  # 첨부만 하고 바로 보낸 경우
            text = "첨부한 자료 내용을 공부 노트로 정리해줘."
        # 화면엔 짧게, 모델에겐 본문(이미지는 그림 그대로)까지
        shown = text + "".join(f"\n{a.summary}" for a in self.attachments)
        content = build_user_content(text, self.attachments)
        has_files = bool(self.attachments)
        self.attachments.clear()
        self._refresh_attach_bar()

        self.entry.delete(0, "end")
        self._add_bubble(shown, "user")
        self._set_busy(True)
        threading.Thread(target=self._worker, args=(content, has_files), daemon=True).start()

    def _worker(self, content, has_files: bool = False) -> None:
        """작업 스레드: 화면을 직접 만지지 않고 events 큐에만 넣는다.
        content: 문자열, 또는 이미지가 섞이면 [텍스트, 이미지, …] 목록"""
        if has_files:
            drop_old_pdf_bodies(self.messages)  # 예전 첨부 본문·이미지는 빼서 비용이 안 쌓이게
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": content})
        try:
            answer = run_turn(
                self.client, self.messages,
                on_tool=lambda name, args: self.events.put(("tool", f"🔧 {name} {args}")),
                # 글 조각이 오는 대로 큐에 넣음 → 메인 스레드가 0.1초마다 말풍선에 이어 붙임
                on_text=lambda kind, text: self.events.put(("s_" + kind, text)),
            )
            self.events.put(("final", answer))
        except Exception as e:
            del self.messages[checkpoint:]  # 실패한 질문은 기록에서 제거
            self.events.put(("error", friendly_error(e)))
        finally:
            self.events.put(("done", None))

    def _poll_events(self) -> None:
        """메인 스레드: 큐에 쌓인 결과를 화면에 그린다."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "done":
                    self._finish_stream(self.stream_buf)  # 오류로 중간에 끊겨도 받은 데까지는 남김
                    self.last_streamed = None
                    self._set_busy(False)
                elif kind == "call":  # 다른 스레드가 맡긴 화면 작업 (정리 카드 등)
                    payload()
                elif kind == "s_delta":  # 스트리밍 글 조각
                    if self.stream_label is None:
                        self.stream_label = self._add_bubble("", "bot")
                    self.stream_buf += payload
                    self.stream_dirty = True
                elif kind == "s_end":  # 이 단계 글 끝 → 말풍선 마무리
                    self._finish_stream(payload)
                    self.last_streamed = payload
                elif kind == "final":  # 최종 답. 이미 흘려보낸 글이면 말풍선을 또 만들지 않음
                    if payload and payload != self.last_streamed:
                        self._add_bubble(payload, "bot")
                else:
                    self._add_bubble(payload, kind)
        except queue.Empty:
            pass
        # 조각이 많이 와도 0.1초에 한 번만 다시 그림 (매 조각마다 그리면 화면이 버벅임)
        if self.stream_dirty and self.stream_label is not None:
            self.stream_label.configure(text=pretty_markdown(self.stream_buf) + " ▌")  # ▌ = 쓰는 중 표시
            self.stream_dirty = False
            self._scroll_bottom()
        self._update_status()
        self.after(100, self._poll_events)

    def _finish_stream(self, text: str) -> None:
        """스트리밍 중인 말풍선을 완성된 글로 고정하고 상태를 비움."""
        if self.stream_label is not None:
            self.stream_label.configure(text=pretty_markdown(text or self.stream_buf))
            self._scroll_bottom()
        self.stream_label, self.stream_buf, self.stream_dirty = None, "", False

    def _update_status(self) -> None:
        """창 아래 상태줄: '생각 중...' 과 노션 동기화 진행률을 같이 보여줌."""
        parts = []
        if self.busy:
            parts.append("생각 중...")
        s = notion_tools.SYNC_STATE
        if s["running"]:
            parts.append(f"📚 노션 노트 동기화 중 {s['done']}/{s['total']}" if s["total"] else "📚 노션 노트 확인 중...")
        text = "   ·   ".join(parts)
        if self.status.cget("text") != text:  # 매번 다시 그리면 깜빡여서 바뀔 때만
            self.status.configure(text=text)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_btn.configure(state="disabled" if busy else "normal")
        self.attach_btn.configure(state="disabled" if busy else "normal")
        if not busy:
            self.entry.focus()


def main() -> None:
    ctk.set_appearance_mode("system")  # 윈도우 라이트/다크 모드 따라감
    AssistantApp().mainloop()


if __name__ == "__main__":
    main()

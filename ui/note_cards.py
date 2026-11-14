"""PDF 정리 카드 (GUI 부품).

PDF 하나(또는 하루치로 묶은 PDF들)마다 카드 하나:
  [📝 lecture.pdf]
  제목 [80일차]  날짜 [2026-11-03]  [강의|실습]
  ┌ 정리본 (직접 고칠 수 있음) ┐
  └────────────────────────────┘
  [Notion에 저장]  상태

[스레드 규칙] tkinter 화면은 메인 스레드에서만 건드려야 해서,
오래 걸리는 일(모델 호출, Notion 저장)은 스레드에서 하고
화면 갱신은 run_on_ui(함수) 로 메인 스레드에 맡김. (app.py 의 events 큐가 실행해 줌)
"""

import threading
from typing import Callable

import customtkinter as ctk

import tools.notion as notion_tools
from core.agent import friendly_error, summarize_pdfs
from study import templates


class NoteCard(ctk.CTkFrame):
    def __init__(self, master, *, attachments: list, title: str, date: str, client, template,
                 run_on_ui: Callable[[Callable], None], font, small_font):
        super().__init__(master, corner_radius=14, border_width=1, border_color=("#cbd5e1", "#4b5563"))
        self.attachments, self.client, self.run_on_ui = attachments, client, run_on_ui
        self.template = template  # templates.Template: 정리 형식 + Notion 분류/유형
        self.initial_title = title  # 스레드에서 입력칸(위젯)을 읽으면 안 돼서 값으로 따로 보관
        self.grid_columnconfigure(0, weight=1)

        names = ", ".join(a.name for a in attachments)
        icon = templates.TAB_ICON.get(template.category, "📄")
        ctk.CTkLabel(self, text=f"{names}  →  {icon} {template.category} 탭 ({template.name})",
                     font=small_font, anchor="w").grid(
            row=0, column=0, padx=12, pady=(10, 4), sticky="ew")

        # 제목 / 날짜 / 유형
        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=0, padx=12, sticky="ew")
        ctk.CTkLabel(meta, text="제목", font=small_font).pack(side="left")
        self.title_entry = ctk.CTkEntry(meta, width=90, font=small_font)
        self.title_entry.insert(0, title)
        self.title_entry.pack(side="left", padx=(4, 10))
        ctk.CTkLabel(meta, text="날짜", font=small_font).pack(side="left")
        self.date_entry = ctk.CTkEntry(meta, width=105, font=small_font, placeholder_text="YYYY-MM-DD")
        self.date_entry.insert(0, date)
        self.date_entry.pack(side="left", padx=(4, 10))
        # 유형 기본값은 템플릿에서 (강의노트=강의, 회의록=실습, 주간회고=없음)
        self.type_seg = ctk.CTkSegmentedButton(meta, values=["강의", "실습", "없음"], font=small_font)
        self.type_seg.set(template.note_type or "없음")
        if "주차" in template.title_rule:
            self.title_entry.configure(width=110)  # '16주차 회고'가 안 잘리게
        self.type_seg.pack(side="left")

        # 정리본 (수정 가능)
        self.text = ctk.CTkTextbox(self, height=280, font=font, wrap="word")
        self.text.grid(row=2, column=0, padx=12, pady=8, sticky="ew")
        self.text.insert("1.0", "정리하는 중... (PDF 분량에 따라 30초~1분)")
        self.text.configure(state="disabled")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, padx=12, pady=(0, 10), sticky="ew")
        self.save_btn = ctk.CTkButton(bottom, text="Notion에 저장", width=110, font=small_font,
                                      state="disabled", command=self.save)
        self.save_btn.pack(side="left")
        self.status = ctk.CTkLabel(bottom, text="", font=small_font, text_color=("#6b7280", "#9ca3af"))
        self.status.pack(side="left", padx=10)

        threading.Thread(target=self._summarize, daemon=True).start()

    # ------------------------------------------------------------ 정리 (스레드)
    def _summarize(self) -> None:
        try:
            md = summarize_pdfs(self.client, self.attachments, self.initial_title, self.template.name)
            self.run_on_ui(lambda: self._show_draft(md))
        except Exception as e:
            msg = friendly_error(e)
            self.run_on_ui(lambda: self._show_draft(None, error=msg))

    def _show_draft(self, md: str | None, error: str = "") -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if md:
            self.text.insert("1.0", md)
            self.text.see("1.0")  # 처음부터 읽을 수 있게 맨 위로
            self.save_btn.configure(state="normal")
            self.status.configure(text="내용 확인 후 저장하세요 (직접 수정 가능)")
        else:
            self.text.insert("1.0", f"정리 실패: {error}")
            self.status.configure(text="⚠️ 실패")

    # ------------------------------------------------------------ 저장 (스레드)
    def save(self) -> None:
        title = self.title_entry.get().strip()
        content = self.text.get("1.0", "end").strip()
        if not title or not content:
            self.status.configure(text="제목과 내용이 필요해요")
            return
        self.save_btn.configure(state="disabled")
        self.status.configure(text="저장 중...")
        args = (title, content, self.type_seg.get(), self.date_entry.get().strip())
        threading.Thread(target=self._save, args=args, daemon=True).start()

    def _save(self, title, content, note_type, date) -> None:
        try:
            r = notion_tools.create_note(title, content, note_type, date,
                                         self.template.category, self.template.topic)
            self.run_on_ui(lambda: self._saved(r["url"]))
        except Exception as e:
            msg = str(e)
            self.run_on_ui(lambda: self._save_failed(msg))

    def _saved(self, url: str) -> None:
        self.save_btn.configure(text="✅ 저장됨")
        self.status.configure(text=url or "Notion에 저장됨")
        self.text.configure(state="disabled")

    def _save_failed(self, msg: str) -> None:
        self.save_btn.configure(state="normal")
        self.status.configure(text=f"⚠️ {msg[:80]}")

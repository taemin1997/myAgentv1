"""첨부 파일(PDF) → 텍스트.

[왜 텍스트로 뽑아서 넣나]
모델에게 PDF 파일 자체를 보내는 방법도 있지만, 텍스트로 뽑아서 메시지에 넣으면
- 무엇이 모델에게 전달되는지 눈으로 확인 가능하고
- 토큰(비용)이 훨씬 적게 듦.
대신 글자가 이미지로 된 PDF(스캔본, 캡처 슬라이드)는 텍스트가 거의 안 나옴 → 경고로 알려줌.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

MAX_CHARS = 150_000  # 이보다 길면 잘라냄 (모델 입력 한도 + 비용 고려)


@dataclass
class Attachment:
    name: str
    pages: int
    text: str
    warning: str = ""

    def to_prompt(self) -> str:
        """사용자 메시지 뒤에 붙일 형식. 페이지 구분을 남겨서 '몇 페이지 내용인지' 모델이 알 수 있게 함."""
        return f"\n\n[첨부 PDF: {self.name} ({self.pages}페이지)]\n<<<\n{self.text}\n>>>"


_BODY = re.compile(r"(\[첨부 PDF: [^\]]+\])\n<<<\n.*?\n>>>", re.DOTALL)


def drop_old_pdf_bodies(messages: list) -> None:
    """대화 기록에 남은 예전 PDF 본문을 지움 (제목 줄만 남김).

    대화 기록은 매 질문마다 통째로 모델에 다시 보내지기 때문에, PDF 여러 개를 이어서 정리하면
    예전 PDF 본문 때문에 비용이 계속 불어남. 새 PDF를 첨부할 때 이전 것들을 비워 두면
    지금 정리 중인 PDF만 남음. (이미 만든 정리본은 assistant 답변에 남아 있어서 괜찮음)
    """
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str) and "<<<" in m["content"]:
            m["content"] = _BODY.sub(r"\1 (이전 첨부라 본문은 생략됨)", m["content"])


def load_pdf(path: str | Path) -> Attachment:
    path = Path(path)
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(f"--- p.{i} ---\n{text}")
    text = "\n\n".join(parts)

    warning = ""
    n = len(reader.pages)
    if len(text) < 30 * max(n, 1):  # 페이지당 30자도 안 나오면 이미지 PDF일 가능성이 큼
        warning = "텍스트가 거의 없음 (스캔/이미지 PDF일 수 있음)"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…(너무 길어서 이후 생략)"
        warning = (warning + ", " if warning else "") + f"{MAX_CHARS:,}자 이후 생략됨"
    return Attachment(name=path.name, pages=n, text=text, warning=warning)

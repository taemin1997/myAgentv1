"""첨부 파일 → 모델에게 보낼 내용.

지원 형식
- 문서: PDF, Word(.docx), PowerPoint(.pptx), HTML, 텍스트류(.txt .md .csv .json .py …)
  → 글자만 뽑아서 메시지에 붙임
- 이미지: .png .jpg .jpeg .webp .gif
  → 그림 그대로 모델에 보냄 (아키텍처 그림, 화이트보드 사진, 캡처 등)

[왜 문서는 텍스트로 뽑아서 넣나]
파일 자체를 보내는 것보다
- 무엇이 모델에게 전달되는지 눈으로 확인 가능하고
- 토큰(비용)이 훨씬 적게 듦.
대신 글자가 이미지로 된 PDF(스캔본, 캡처 슬라이드)는 텍스트가 거의 안 나옴 → 경고로 알려줌.
이런 건 페이지를 캡처해서 이미지로 첨부하면 모델이 그림으로 읽을 수 있음.

[이미지는 왜 따로]
그림 속 글자·도형은 텍스트로 못 뽑아서, 모델이 직접 보게 해야 함. 그래서 메시지를
'텍스트 조각 + 이미지 조각' 목록으로 보냄 (OpenAI 멀티모달 메시지 형식).
이미지는 텍스트보다 비싸서, 다음 첨부 때 예전 이미지는 대화 기록에서 빼 줌 (drop_old_pdf_bodies).
"""

import base64
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from pypdf import PdfReader

MAX_CHARS = 150_000  # 이보다 길면 잘라냄 (모델 입력 한도 + 비용 고려)
MAX_IMAGE_MB = 15    # OpenAI 이미지 한도(20MB)보다 여유 있게

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".py", ".sql", ".yaml", ".yml",
            ".ipynb", ".js", ".ts", ".java", ".xml", ".ini", ".toml"}
HTML_EXT = {".html", ".htm"}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}
SUPPORTED = {".pdf", ".docx", ".pptx", *TEXT_EXT, *HTML_EXT, *IMAGE_EXT}
# 파일 선택 창에 쓰는 형식 목록
FILE_TYPES = [
    ("지원하는 파일", " ".join(f"*{e}" for e in sorted(SUPPORTED))),
    ("PDF", "*.pdf"), ("Word / PowerPoint", "*.docx *.pptx"), ("HTML", "*.html *.htm"),
    ("이미지", " ".join(f"*{e}" for e in IMAGE_EXT)), ("텍스트", " ".join(f"*{e}" for e in sorted(TEXT_EXT))),
    ("모든 파일", "*.*"),
]


@dataclass
class Attachment:
    name: str
    pages: int        # PDF·PPT 쪽수 (나머지는 0)
    text: str         # 문서에서 뽑은 글자 (이미지는 빈 문자열)
    warning: str = ""
    kind: str = "문서"  # 문서 / 이미지
    image_url: str = ""  # 이미지면 data:image/png;base64,... (모델에 그대로 보냄)

    @property
    def summary(self) -> str:
        """첨부창에 보여줄 한 줄 설명."""
        if self.kind == "이미지":
            return f"🖼️ {self.name} (이미지)"
        size = f"{self.pages}쪽, " if self.pages else ""
        return f"📎 {self.name} ({size}{len(self.text):,}자)"

    def to_prompt(self) -> str:
        """사용자 메시지 뒤에 붙일 텍스트. 이미지는 '아래 그림 참고' 표시만 (그림은 image_part로 따로 감)."""
        if self.kind == "이미지":
            return f"\n\n[첨부 이미지: {self.name}] (함께 보낸 그림 참고)"
        pages = f" ({self.pages}페이지)" if self.pages else ""
        return f"\n\n[첨부 파일: {self.name}{pages}]\n<<<\n{self.text}\n>>>"

    def image_part(self) -> dict:
        return {"type": "image_url", "image_url": {"url": self.image_url}}


def build_user_content(text: str, attachments: list[Attachment]) -> str | list:
    """사용자 메시지 내용 만들기. 이미지가 없으면 그냥 문자열(예전과 같음),
    이미지가 있으면 [텍스트 조각, 이미지 조각, …] 목록 (OpenAI 멀티모달 형식)."""
    body = text + "".join(a.to_prompt() for a in attachments)
    images = [a.image_part() for a in attachments if a.kind == "이미지"]
    if not images:
        return body
    return [{"type": "text", "text": body}, *images]


_BODY = re.compile(r"(\[첨부 (?:PDF|파일): [^\]]+\])\n<<<\n.*?\n>>>", re.DOTALL)


def drop_old_pdf_bodies(messages: list) -> None:
    """대화 기록에 남은 예전 첨부 본문·이미지를 지움 (제목 줄만 남김).

    대화 기록은 매 질문마다 통째로 모델에 다시 보내지기 때문에, 파일 여러 개를 이어서 정리하면
    예전 본문 때문에 비용이 계속 불어남. 새 파일을 첨부할 때 이전 것들을 비워 두면
    지금 정리 중인 파일만 남음. (이미 만든 정리본은 assistant 답변에 남아 있어서 괜찮음)
    """
    for m in messages:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and "<<<" in c:
            m["content"] = _BODY.sub(r"\1 (이전 첨부라 본문은 생략됨)", c)
        elif isinstance(c, list):  # 이미지가 섞인 메시지 → 글자만 남기고 그림은 빼기
            texts = [p["text"] for p in c if p.get("type") == "text"]
            m["content"] = _BODY.sub(r"\1 (이전 첨부라 본문은 생략됨)", "\n".join(texts)) \
                + "\n(이전 첨부 이미지는 생략됨)"


# ================================================================ 형식별 읽기
def load_file(path: str | Path) -> Attachment:
    """확장자를 보고 알맞게 읽음. 지원 안 하는 형식이면 ValueError."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return load_pdf(path)
    if ext in IMAGE_EXT:
        return _load_image(path, IMAGE_EXT[ext])
    if ext == ".docx":
        return _finish(path.name, _docx_text(path))
    if ext == ".pptx":
        text, n = _pptx_text(path)
        return _finish(path.name, text, pages=n)
    if ext in HTML_EXT:
        return _finish(path.name, _html_text(_read_text(path)))
    if ext in TEXT_EXT:
        return _finish(path.name, _read_text(path))
    if ext in (".hwp", ".hwpx", ".doc", ".ppt", ".xls", ".xlsx"):
        raise ValueError(f"{ext} 은 아직 못 읽음. PDF로 저장해서 첨부해 주세요")
    raise ValueError(f"{ext or '확장자 없는'} 파일은 지원하지 않음")


def _finish(name: str, text: str, pages: int = 0) -> Attachment:
    """공통 마무리: 빈 내용 경고 + 너무 길면 자르기."""
    text = text.strip()
    warning = "" if text else "글자가 없음"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…(너무 길어서 이후 생략)"
        warning = f"{MAX_CHARS:,}자 이후 생략됨"
    return Attachment(name=name, pages=pages, text=text, warning=warning)


def _read_text(path: Path) -> str:
    """텍스트 파일 읽기. 윈도우에서 만든 파일은 cp949(한글 윈도우 기본)일 수 있어서 차례로 시도."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


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
        warning = "텍스트가 거의 없음 (스캔/이미지 PDF면 캡처해서 이미지로 첨부)"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…(너무 길어서 이후 생략)"
        warning = (warning + ", " if warning else "") + f"{MAX_CHARS:,}자 이후 생략됨"
    return Attachment(name=path.name, pages=n, text=text, warning=warning)


def _load_image(path: Path, mime: str) -> Attachment:
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_MB * 1024 * 1024:
        raise ValueError(f"이미지가 너무 큼 ({len(data) / 1024 / 1024:.1f}MB, 최대 {MAX_IMAGE_MB}MB)")
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return Attachment(name=path.name, pages=0, text="", kind="이미지", image_url=url)


# --- HTML: 태그를 빼고 글자만. 제목·문단·표 줄바꿈은 살림
class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "table", "ul", "ol", "pre", "blockquote", "hr", "header", "footer", "nav"}

    def __init__(self):
        super().__init__()
        self.out, self.skip, self.title = [], 0, ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        if tag in ("h1", "h2", "h3"):
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def _html_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)          # 연속 공백 하나로
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)  # 빈 줄 여러 개 → 하나
    lines = [l.strip() for l in text.splitlines()]
    body = "\n".join(lines).strip()
    return (f"# {p.title.strip()}\n\n" if p.title.strip() else "") + body


# --- Word / PowerPoint: 둘 다 zip 안의 XML 파일이라 표준 라이브러리로 읽을 수 있음
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx_text(path: Path) -> str:
    """word/document.xml 에서 문단(w:p)과 표(w:tbl)를 순서대로 꺼냄."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    lines = []

    def para(p) -> str:
        return "".join(t.text or "" for t in p.iter(f"{W}t"))

    for el in body:
        if el.tag == f"{W}p":
            t = para(el)
            style = el.find(f"{W}pPr/{W}pStyle")
            # 제목 스타일(Heading1, 제목1 …)이면 마크다운 제목처럼 표시해서 구조를 살림
            if t and style is not None and re.search(r"(?i)heading|제목", style.get(f"{W}val", "")):
                level = re.search(r"\d", style.get(f"{W}val", "")) or None
                t = "#" * min(int(level.group()) if level else 2, 3) + " " + t
            lines.append(t)
        elif el.tag == f"{W}tbl":
            for row in el.iter(f"{W}tr"):
                cells = [" ".join(para(p) for p in c.iter(f"{W}p")).strip() for c in row.iter(f"{W}tc")]
                lines.append("| " + " | ".join(cells) + " |")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def _pptx_text(path: Path) -> tuple[str, int]:
    """ppt/slides/slideN.xml 마다 글자(a:t)를 문단 단위로 꺼냄. (텍스트, 슬라이드 수)"""
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                       key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
        parts = []
        for n in names:
            i = int(re.search(r"\d+", n.rsplit("/", 1)[1]).group())  # 파일 이름의 번호 = 슬라이드 번호
            root = ET.fromstring(z.read(n))
            paras = ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in root.iter(f"{A}p")]
            text = "\n".join(p for p in paras if p.strip())
            if text:
                parts.append(f"--- 슬라이드 {i} ---\n{text}")
    return "\n\n".join(parts), len(names)

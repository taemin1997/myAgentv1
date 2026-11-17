/* 브라우저 미리보기용 가짜 api (index.html?mock 일 때만 불러옴. 실제 앱에선 안 씀).
 * 파이썬·OpenAI·Notion 없이 화면 모양과 스트리밍·카드 흐름만 확인하려고 만듦.
 */
(() => {
  const queue = [];
  let busy = false;
  let atts = [];
  const templates = ["📅 일차 노트 · 강의", "📅 일차 노트 · 회의록", "📝 회고", "🚀 프로젝트", "🎯 면접"];
  const push = (ev) => queue.push(ev);
  const later = (ms, fn) => setTimeout(fn, ms);

  const SAMPLE = [
    "22일차 노트에서 찾았어. **그래디언트 소실**은 층이 깊어질수록 앞쪽 층의 기울기가 0에 가까워지는 문제임.",
    "",
    "### 왜 생기나",
    "- 시그모이드 미분값이 최대 `0.25`라서 곱할수록 작아짐",
    "- 층이 많으면 곱셈이 반복됨",
    "  - 그래서 ReLU를 씀",
    "",
    "| 방법 | 효과 |",
    "|---|---|",
    "| ReLU | 미분값 1 유지 |",
    "| 배치 정규화 | 분포 안정 |",
    "",
    "- [x] 22일차 복습",
    "- [ ] 퀴즈 풀기",
    "",
    "```python",
    "# 왜: 시그모이드 대신 ReLU 를 써야 기울기가 안 줄어듦",
    "x = torch.relu(x)",
    "```",
    "",
    "자세한 건 https://example.com 참고.",
  ].join("\n");

  function stream(text) {
    let i = 0;
    const step = () => {
      if (i >= text.length) {
        push({ type: "s_end", text });
        push({ type: "final", text });
        push({ type: "done" });
        busy = false;
        return;
      }
      const n = 3 + Math.floor(Math.random() * 6);
      push({ type: "s_delta", text: text.slice(i, i + n) });
      i += n;
      later(15, step);
    };
    step();
  }

  const api = {
    init: async () => ({ model: "gpt-5.4-mini", heavy_model: "gpt-5.4", greeting: "안녕하세요! (미리보기 모드)", templates, has_key: true }),
    poll: async () => ({
      events: queue.splice(0), busy, status: busy ? "생각 중...   ·   📚 노션 노트 동기화 중 12/180" : "",
      usage: { label: "이번 대화 $0.0123 · 오늘 $0.21", tip: "미리보기 모드 가짜 값" },
    }),
    send: async (text) => {
      if (busy || !(text.trim() || atts.length)) return { ok: false };
      busy = true;
      const shown = (text.trim() || "첨부한 자료 내용을 공부 노트로 정리해줘.") + atts.map((a) => "\n" + a.summary).join("");
      atts = [];
      if (/갱신/.test(text)) push({ type: "heavy", text: "⚡ 갱신 작업이라 상위 모델로 전환", model: "gpt-5.4" });
      later(300, () => push({ type: "tool", name: "search_study_notes", args: '{"query": "그래디언트 소실", "limit": 5}' }));
      later(700, () => stream(SAMPLE));
      return { ok: true, shown };
    },
    new_chat: async () => ({ ok: !busy, message: "새 대화를 시작합니다. 무엇을 도와드릴까요?" }),
    save_api_key: async () => ({ ok: true, message: "API 키를 저장했어요." }),
    save_notion_token: async () => ({ ok: true, message: "Notion 토큰을 저장했어요." }),
    open_url: async (u) => console.log("open_url", u),
    add_files: async (names) => {
      for (const n of names) atts.push({ summary: `[첨부 파일: ${n}] (3쪽, 1,200자)`, warning: "" });
      return { items: atts, templates };
    },
    pick_files: async () => api.add_files(["회의록_1114.docx", "기획서.pdf"]),
    clear_attachments: async () => ((atts = []), { items: [], templates }),
    make_note_cards: async (label, merge) => {
      const groups = merge ? [atts] : atts.map((a) => [a]);
      const cards = groups.map((g, k) => ({
        id: k + 1, header: g.map((a) => a.summary).join(", ") + "  →  📅 일차 노트 탭", title: `${89 + k}일차`,
        date: "2026-11-16", note_type: "실습", wide_title: false,
      }));
      cards.forEach((c) => later(1200, () => push({ type: "card_draft", id: c.id, text: SAMPLE })));
      atts = [];
      return { ok: true, cards, attachments: { items: [], templates } };
    },
    save_card: async (id) => {
      later(600, () => push({ type: "card_saved", id, url: "https://www.notion.so/example" }));
      return { ok: true };
    },
  };

  window.__mockDrop = (e) => {
    const names = [...(e.dataTransfer?.files || [])].map((f) => f.name);
    if (names.length) api.add_files(names).then((r) => push({ type: "attachments", ...r }));
  };
  window.pywebview = { api };
  window.dispatchEvent(new Event("pywebviewready"));
})();

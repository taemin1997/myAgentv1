/* 개인 비서 웹 화면.
 *
 * 파이썬(ui/web_app.py 의 Api)과 주고받는 방법:
 *   - 화면 → 파이썬: api.이름(...)  (Promise 를 돌려줌)
 *   - 파이썬 → 화면: 0.1초마다 api.poll() 로 쌓인 이벤트를 한꺼번에 가져옴
 *     스트리밍 조각이 아무리 많이 와도 말풍선은 poll 한 번에 한 번만 다시 그림 (매 조각마다 그리면 버벅임)
 * 브라우저에서 화면만 볼 땐 index.html?mock 으로 열면 mock.js 의 가짜 api 를 씀.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const chat = $("chat");
  const input = $("input");
  let api = null;

  // 스트리밍 상태: 지금 글자가 채워지는 말풍선과 그 안의 글
  const stream = { el: null, buf: "", dirty: false, last: null };
  const cards = new Map(); // 카드 id → 카드 화면 요소들
  let busy = false;

  // ------------------------------------------------------------ 그리기 도우미
  // 사용자가 위로 올려서 예전 글을 읽는 중이면 억지로 맨 아래로 내리지 않음
  const nearBottom = () => chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  function append(el) {
    const stick = nearBottom();
    chat.appendChild(el);
    if (stick) chat.scrollTop = chat.scrollHeight;
    return el;
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const addUser = (text) => append(el("div", "msg user", text));
  const addError = (text) => append(el("div", "msg error", text));
  const addNotice = (text) => append(el("div", "notice", text));
  function addBot(text) {
    const b = el("div", "msg bot md");
    b.innerHTML = window.renderMarkdown(text);
    return append(b);
  }
  function addTool(name, args, heavy) {
    // 지금은 '🔧 이름 인자'. 캐릭터 단계에서 담당 캐릭터 얼굴 + 한 줄로 바뀔 자리
    const t = el("div", "tool" + (heavy ? " heavy" : ""));
    t.appendChild(el("span", "", heavy ? "⚡" : "🔧"));
    t.appendChild(el("span", "tname", name));
    if (args && args !== "{}") {
      const a = el("span", "targs", args);
      a.title = "눌러서 인자 전체 보기";
      a.onclick = () => a.classList.toggle("open");
      t.appendChild(a);
    }
    append(t);
  }

  // ------------------------------------------------------------ 스트리밍
  function finishStream(text) {
    if (stream.el) {
      stream.el.classList.remove("streaming");
      stream.el.innerHTML = window.renderMarkdown(text || stream.buf);
    }
    stream.el = null;
    stream.buf = "";
    stream.dirty = false;
  }

  function handle(ev) {
    switch (ev.type) {
      case "s_delta":
        if (!stream.el) {
          stream.el = append(el("div", "msg bot md streaming"));
        }
        stream.buf += ev.text;
        stream.dirty = true;
        break;
      case "s_end": // 이 단계 글 끝 → 말풍선 마무리
        finishStream(ev.text);
        stream.last = ev.text;
        break;
      case "final": // 최종 답. 이미 흘려보낸 글이면 말풍선을 또 만들지 않음
        if (ev.text && ev.text !== stream.last) addBot(ev.text);
        break;
      case "done":
        finishStream(stream.buf); // 오류로 중간에 끊겨도 받은 데까지는 남김
        stream.last = null;
        $("heavy-chip").hidden = true;
        break;
      case "tool":
        addTool(ev.name, ev.args);
        break;
      case "heavy": // 갱신 작업이라 상위 모델로 전환
        $("heavy-chip").textContent = "⚡ " + ev.model;
        $("heavy-chip").hidden = false;
        addTool(ev.text.replace(/^⚡\s*/, ""), "", true);
        break;
      case "error":
        addError(ev.text);
        break;
      case "notice": // 오래된 대화 요약 같은 안내
        addNotice(ev.text);
        break;
      case "attachments": // 끌어다 놓기는 파이썬 쪽에서 받아서 이걸로 알려 줌
        renderAttach(ev);
        break;
      case "card_draft":
        cardDraft(ev);
        break;
      case "card_saved":
        cardSaved(ev);
        break;
      case "card_failed":
        cardFailed(ev);
        break;
      case "issue_draft": // 모델이 만든 이슈 초안 → 확인 카드 (올리기는 사람이 버튼으로만)
        addIssueCard(ev);
        break;
      case "issue_posted":
      case "issue_failed":
        issueResult(ev);
        break;
      case "issue_removal": // 닫기·삭제 확인 카드
        addRemovalCard(ev);
        break;
      case "issue_removed":
      case "issue_remove_failed":
        removalResult(ev);
        break;
    }
  }

  async function pollLoop() {
    try {
      const r = await api.poll();
      for (const ev of r.events) handle(ev);
      if (stream.dirty && stream.el) {
        const stick = nearBottom();
        stream.el.innerHTML = window.renderMarkdown(stream.buf);
        stream.dirty = false;
        if (stick) chat.scrollTop = chat.scrollHeight;
      }
      setBusy(r.busy);
      if ($("status").textContent !== r.status) $("status").textContent = r.status;
      if (r.usage && $("cost-chip").textContent !== r.usage.label) {
        $("cost-chip").textContent = r.usage.label; // 이번 대화 · 오늘 비용. 마우스를 올리면 토큰·모델별·이번 달
        $("cost-chip").title = r.usage.tip;
      }
    } catch (e) {
      console.error(e);
    }
    setTimeout(pollLoop, 100);
  }

  function setBusy(b) {
    if (b === busy) return;
    busy = b;
    $("btn-send").disabled = b;
    $("btn-attach").disabled = b;
    $("btn-new").disabled = b;
    if (!b) input.focus();
  }

  // ------------------------------------------------------------ 전송
  async function send() {
    const text = input.value;
    const r = await api.send(text);
    if (r.need_key) return askSecret("key");
    if (!r.ok) return;
    addUser(r.shown);
    chat.scrollTop = chat.scrollHeight; // 내가 보낸 건 항상 보이게
    input.value = "";
    autoGrow();
    renderAttach({ items: [] });
    setBusy(true);
  }

  function autoGrow() {
    input.style.height = "auto";
    if (!input.value) return; // 비어 있으면 CSS 기본 높이 (긴 안내 문구 때문에 커지지 않게)
    input.style.height = Math.min(input.scrollHeight + 2, 160) + "px";
  }

  // ------------------------------------------------------------ 첨부
  function renderAttach(data) {
    const items = data.items || [];
    $("attach-bar").hidden = items.length === 0;
    const ul = $("attach-list");
    ul.replaceChildren(
      ...items.map((a) => {
        const li = el("li", "", a.summary);
        if (a.warning) li.appendChild(el("span", "warn", "  ⚠️ " + a.warning));
        return li;
      })
    );
    if (data.templates) fillTemplates(data.templates);
    if (items.length) input.focus();
  }

  function fillTemplates(labels) {
    const sel = $("template-select");
    const keep = sel.value;
    sel.replaceChildren(...labels.map((l) => Object.assign(el("option", "", l), { value: l })));
    if (labels.includes(keep)) sel.value = keep;
  }

  async function makeCards() {
    const r = await api.make_note_cards($("template-select").value, $("merge").checked);
    if (r.need_key) return askSecret("key");
    if (!r.ok) return;
    for (const c of r.cards) addCard(c);
    addNotice(`정리 카드 ${r.cards.length}개를 만들었어요. 제목·날짜를 확인하고 하나씩 저장하세요. ` +
      "(날짜는 평일 기준 추정이라 휴일이 끼면 고쳐 주세요)");
    renderAttach(r.attachments);
  }

  // ------------------------------------------------------------ 정리 카드
  function addCard(c) {
    const card = el("section", "card");
    card.appendChild(el("div", "head", "📝 " + c.header));

    const meta = el("div", "meta");
    const title = Object.assign(el("input", c.wide_title ? "wide" : ""), { type: "text", value: c.title });
    const date = Object.assign(el("input", "date"), { type: "text", value: c.date, placeholder: "YYYY-MM-DD" });
    meta.append("제목", title, "날짜", date);
    // 유형 기본값은 템플릿에서 (강의노트=강의, 회의록=실습, 주간회고=없음)
    const seg = el("div", "seg");
    let noteType = c.note_type;
    for (const v of ["강의", "실습", "없음"]) {
      const b = el("button", v === noteType ? "on" : "", v);
      b.type = "button";
      b.onclick = () => {
        noteType = v;
        seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      };
      seg.appendChild(b);
    }
    meta.appendChild(seg);
    card.appendChild(meta);

    const text = el("textarea");
    text.value = "정리하는 중... (자료 분량에 따라 30초~1분)";
    text.disabled = true;
    const preview = el("div", "preview md");
    preview.hidden = true;
    card.append(text, preview);

    const foot = el("div", "foot");
    const save = el("button", "btn small primary", "Notion에 저장");
    save.disabled = true;
    const toggle = el("button", "btn small ghost", "미리보기");
    toggle.disabled = true;
    toggle.onclick = () => {
      const show = preview.hidden;
      if (show) preview.innerHTML = window.renderMarkdown(text.value);
      preview.hidden = !show;
      text.hidden = show;
      toggle.textContent = show ? "고치기" : "미리보기";
    };
    const state = el("span", "state");
    foot.append(save, toggle, state);
    card.appendChild(foot);

    save.onclick = async () => {
      const r = await api.save_card(c.id, title.value, text.value, noteType, date.value);
      if (!r.ok) return (state.textContent = r.message || "저장 못 함");
      save.disabled = true;
      state.textContent = "저장 중...";
    };

    cards.set(c.id, { text, save, toggle, state });
    append(card);
  }

  function cardDraft(ev) {
    const c = cards.get(ev.id);
    if (!c) return;
    if (ev.text) {
      c.text.value = ev.text;
      c.text.disabled = false;
      c.text.scrollTop = 0; // 처음부터 읽을 수 있게 맨 위로
      c.save.disabled = false;
      c.toggle.disabled = false;
      c.state.textContent = "내용 확인 후 저장하세요 (직접 수정 가능)";
    } else {
      c.text.value = "정리 실패: " + (ev.error || "");
      c.state.textContent = "⚠️ 실패";
    }
  }

  function cardSaved(ev) {
    const c = cards.get(ev.id);
    if (!c) return;
    c.save.textContent = "✅ 저장됨";
    c.text.readOnly = true;
    c.state.replaceChildren();
    if (ev.url) c.state.appendChild(Object.assign(el("a", "", ev.url), { href: ev.url }));
    else c.state.textContent = "Notion에 저장됨";
  }

  function cardFailed(ev) {
    const c = cards.get(ev.id);
    if (!c) return;
    c.save.disabled = false;
    c.state.textContent = "⚠️ " + ev.message;
  }

  // ------------------------------------------------------------ GitHub 이슈 카드
  // 이슈는 팀원 모두에게 알림이 가서, 모델은 초안만 만들고 실제로 올리는 건 이 카드의 버튼뿐
  const issues = new Map(); // 카드 id → 화면 요소들
  function addIssueCard(c) {
    const card = el("section", "card issue");
    card.appendChild(el("div", "head", "🐙 GitHub 이슈 초안 → " + c.repo));

    const title = Object.assign(el("input", "issue-title"), { type: "text", value: c.title, placeholder: "제목" });
    const labels = Object.assign(el("input", "issue-labels"), { type: "text", value: c.labels || "",
      placeholder: "라벨 (쉼표로 구분, 없으면 비움)" });
    const text = el("textarea");
    text.value = c.body;
    const preview = el("div", "preview md");
    preview.hidden = true;
    card.append(title, labels, text, preview);

    const foot = el("div", "foot");
    const post = el("button", "btn small primary", "GitHub에 올리기");
    const toggle = el("button", "btn small ghost", "미리보기");
    toggle.onclick = () => {
      const show = preview.hidden;
      if (show) preview.innerHTML = window.renderMarkdown(text.value);
      preview.hidden = !show;
      text.hidden = show;
      toggle.textContent = show ? "고치기" : "미리보기";
    };
    const drop = el("button", "btn small ghost", "버리기");
    const state = el("span", "state", c.has_token
      ? "내용 확인 후 올리세요 (직접 수정 가능). 팀원 모두에게 알림이 가요"
      : "⚠️ .env에 GITHUB_TOKEN(이슈 쓰기 권한)이 없어서 지금은 못 올려요");
    foot.append(post, toggle, drop, state);
    card.appendChild(foot);

    const lock = () => { title.readOnly = labels.readOnly = text.readOnly = true; post.disabled = drop.disabled = true; };
    drop.onclick = () => { lock(); post.textContent = "버림"; state.textContent = "올리지 않았어요"; };
    post.onclick = async () => {
      const r = await api.post_issue(c.id, title.value, text.value, labels.value);
      if (!r.ok) return (state.textContent = r.message || "못 올림");
      post.disabled = drop.disabled = true;
      state.textContent = "올리는 중...";
    };
    issues.set(c.id, { post, drop, state, lock });
    append(card);
  }

  function issueResult(ev) {
    const c = issues.get(ev.id);
    if (!c) return;
    if (ev.type === "issue_failed") {
      c.post.disabled = c.drop.disabled = false;
      c.state.textContent = "⚠️ " + ev.message;
      return;
    }
    c.lock();
    c.post.textContent = `✅ #${ev.number} 올림`;
    c.state.replaceChildren(Object.assign(el("a", "", ev.url), { href: ev.url }));
  }

  // 닫기·삭제 확인 카드. 삭제는 되돌릴 수 없어서 버튼을 두 번 눌러야 함 (첫 번째는 '정말?'으로 바뀌기만)
  function addRemovalCard(c) {
    const card = el("section", "card issue removal");
    card.appendChild(el("div", "head", `🐙 이슈 닫기·삭제 → ${c.repo}`));
    const info = el("div", "issue-info");
    const link = Object.assign(el("a", "", `#${c.number} ${c.title}`), { href: c.url });
    info.append(link, el("div", "sub",
      `작성자 ${c.author}${c.mine ? " (나)" : ""} · ${c.state === "open" ? "열림" : "닫힘"} · 댓글 ${c.comments}개`));
    card.appendChild(info);

    const foot = el("div", "foot");
    const close = el("button", "btn small", "닫기 (다시 열 수 있음)");
    close.disabled = c.state !== "open";
    const del = el("button", "btn small danger", "🗑 삭제");
    del.disabled = !c.mine;
    const state = el("span", "state", !c.has_token ? "⚠️ .env에 GITHUB_TOKEN이 없어서 지금은 못 해요"
      : c.mine ? "삭제는 되돌릴 수 없어요. 댓글도 같이 사라져요"
      : "내가 쓴 이슈가 아니라서 삭제는 막혀 있어요 (닫기만 가능)");
    foot.append(close, del, state);
    card.appendChild(foot);

    let armed = false; // 삭제 첫 클릭 후 3초 안에 한 번 더 눌러야 진짜 삭제
    let timer = null;
    const run = async (action) => {
      const r = await api.remove_issue(c.id, action);
      if (!r.ok) return (state.textContent = r.message || "못 함");
      close.disabled = del.disabled = true;
      state.textContent = action === "delete" ? "삭제하는 중..." : "닫는 중...";
    };
    close.onclick = () => run("close");
    del.onclick = () => {
      if (!armed) {
        armed = true;
        del.textContent = "정말 삭제 (한 번 더)";
        timer = setTimeout(() => { armed = false; del.textContent = "🗑 삭제"; }, 3000);
        return;
      }
      clearTimeout(timer);
      run("delete");
    };
    issues.set(c.id, { close, del, state, link, armedReset: () => { armed = false; del.textContent = "🗑 삭제"; } });
    append(card);
  }

  function removalResult(ev) {
    const c = issues.get(ev.id);
    if (!c) return;
    if (ev.type === "issue_remove_failed") {
      c.close.disabled = false;
      c.del.disabled = false;
      c.armedReset();
      c.state.textContent = "⚠️ " + ev.message;
      return;
    }
    if (ev.action === "delete") {
      c.del.textContent = "✅ 삭제됨";
      c.link.removeAttribute("href"); // 지운 이슈 링크는 404 라서 글자만 남김
      c.link.classList.add("gone");
      c.state.textContent = "이슈를 지웠어요";
    } else {
      c.close.textContent = "✅ 닫힘";
      c.state.textContent = "닫았어요. GitHub에서 다시 열 수 있어요";
    }
  }

  // ------------------------------------------------------------ 키·토큰 입력
  const SECRETS = {
    key: { title: "OpenAI API 키", desc: "OpenAI API 키를 입력하세요.\n(.env 파일에 저장됩니다)", save: "save_api_key" },
    notion: { title: "Notion 연결", desc: "Notion integration 토큰을 입력하세요.\n(ntn_ 로 시작, .env 파일에 저장됩니다)", save: "save_notion_token" },
  };
  function askSecret(kind) {
    const s = SECRETS[kind];
    const dlg = $("secret-dialog");
    $("secret-title").textContent = s.title;
    $("secret-desc").textContent = s.desc;
    $("secret-input").value = "";
    dlg.onclose = async () => {
      const value = $("secret-input").value;
      $("secret-input").value = ""; // 화면에 값이 남지 않게 바로 비움
      if (dlg.returnValue !== "ok" || !value.trim()) return;
      const r = await api[s.save](value);
      if (r.ok) addNotice(r.message);
    };
    dlg.showModal();
    $("secret-input").focus();
  }

  // ------------------------------------------------------------ 이벤트 연결
  function wire() {
    $("btn-send").onclick = send;
    input.addEventListener("keydown", (e) => {
      // isComposing: 한글 조합 중 Enter 는 글자 확정용이라 보내면 안 됨 (마지막 글자가 따로 한 번 더 감)
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    input.addEventListener("input", autoGrow);

    $("btn-new").onclick = async () => {
      const r = await api.new_chat();
      if (!r.ok) return;
      chat.replaceChildren();
      cards.clear();
      issues.clear();
      addBot(r.message);
    };
    $("btn-key").onclick = () => askSecret("key");
    $("btn-notion").onclick = () => askSecret("notion");
    $("btn-attach").onclick = async () => renderAttach(await api.pick_files());
    $("btn-clear").onclick = async () => renderAttach(await api.clear_attachments());
    $("btn-cards").onclick = makeCards;

    // 말풍선 안 링크는 앱 창 안에서 열리면 채팅 화면이 사라짐 → 기본 브라우저로
    document.addEventListener("click", (e) => {
      const a = e.target.closest("a[href]");
      if (!a) return;
      e.preventDefault();
      api.open_url(a.href);
    });

    // 끌어다 놓기: 여기선 안내 화면만. 실제 파일은 파이썬 쪽 drop 핸들러가 경로로 받음 (web_app._on_drop)
    const overlay = $("drop-overlay");
    let depth = 0; // 자식 요소를 지날 때마다 enter/leave 가 번갈아 와서 깊이로 셈
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    document.addEventListener("dragenter", (e) => {
      if (!hasFiles(e)) return;
      depth++;
      overlay.hidden = busy;
    });
    document.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (!depth) overlay.hidden = true;
    });
    document.addEventListener("dragover", (e) => e.preventDefault()); // 이걸 막아야 drop 이 옴
    document.addEventListener("drop", (e) => {
      e.preventDefault();
      depth = 0;
      overlay.hidden = true;
      if (window.__mockDrop) window.__mockDrop(e); // 브라우저 미리보기용
    });
  }

  async function boot() {
    api = window.pywebview.api;
    const info = await api.init();
    $("model-chip").textContent = info.model;
    $("model-chip").title = `평소 ${info.model} · 최종 프로젝트 갱신 중엔 ${info.heavy_model}`;
    fillTemplates(info.templates);
    addBot(info.greeting);
    wire();
    input.focus();
    pollLoop();
    if (!info.has_key) askSecret("key");
  }

  // pywebview 가 api 를 붙이면 pywebviewready 이벤트가 옴. 이미 붙어 있으면 바로 시작
  let started = false;
  const start = () => { if (!started && window.pywebview?.api?.init) { started = true; boot(); } };
  window.addEventListener("pywebviewready", start);
  if (new URLSearchParams(location.search).has("mock")) {
    const s = document.createElement("script");
    s.src = "mock.js";
    document.head.appendChild(s);
  } else {
    start();
  }
})();

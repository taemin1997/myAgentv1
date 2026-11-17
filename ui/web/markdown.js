/* 작은 마크다운 → HTML 변환기.
 *
 * 외부 라이브러리(marked 등)를 안 쓰는 이유: exe 에 같이 묶을 파일이 줄고, 모델 답에 나오는
 * 문법(제목·굵게·목록·체크박스·표·코드 블록·링크)만 있으면 충분해서.
 * 안전: 먼저 HTML 특수 문자를 전부 이스케이프하고 나서 태그를 붙임 → 모델 답에 <script> 가 있어도 글자로만 보임.
 * 스트리밍 중엔 글이 반쯤 끊겨 있음 → 닫히지 않은 코드 블록은 끝까지 코드로 보고, 표는 구분줄이 오기 전까진 그냥 글.
 */
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  // 한 줄 안의 꾸밈: `코드`, **굵게**, *기울임*, ~~취소선~~, [글](주소), 맨 주소
  function inline(text) {
    const codes = [];
    let s = esc(text).replace(/`([^`]+)`/g, (_, c) => {
      codes.push(c);
      return `\u0000${codes.length - 1}\u0000`; // 코드 안은 다른 꾸밈을 안 하게 잠시 빼 둠
    });
    const links = [];
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => {
      links.push(`<a href="${u}">${t}</a>`);
      return `\u0001${links.length - 1}\u0001`;
    });
    s = s.replace(/(https?:\/\/[^\s<]+[^\s<.,)\]'"])/g, (u) => {
      links.push(`<a href="${u}">${u}</a>`);
      return `\u0001${links.length - 1}\u0001`;
    });
    s = s
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/__(.+?)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/~~(.+?)~~/g, "<del>$1</del>");
    return s
      .replace(/\u0001(\d+)\u0001/g, (_, i) => links[i])
      .replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
  }

  const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const splitRow = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  // 들여쓰기로 중첩된 목록. 체크박스 '- [ ]' / '- [x]' 도 여기서
  function list(items) {
    let html = "";
    const stack = [];
    for (const it of items) {
      while (stack.length && it.indent < stack[stack.length - 1].indent) html += `</li></${stack.pop().tag}>`;
      const top = stack[stack.length - 1];
      if (!top || it.indent > top.indent) {
        const tag = it.ordered ? "ol" : "ul";
        stack.push({ indent: it.indent, tag });
        html += `<${tag}>`;
      } else {
        html += "</li>";
      }
      const task = it.text.match(/^\[( |x|X)\]\s+(.*)$/);
      html += task
        ? `<li class="task"><input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}> ${inline(task[2])}`
        : `<li>${inline(it.text)}`;
    }
    while (stack.length) html += `</li></${stack.pop().tag}>`;
    return html;
  }

  function render(src) {
    const lines = (src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let para = [];
    const flush = () => {
      if (para.length) out.push(`<p>${para.map(inline).join("<br>")}</p>`);
      para = [];
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // ``` 코드 블록
      const fence = line.match(/^\s*```\s*([\w+-]*)/);
      if (fence) {
        flush();
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
        const lang = fence[1] ? `<span class="lang">${esc(fence[1])}</span>` : "";
        out.push(`<pre>${lang}<code>${esc(body.join("\n"))}</code></pre>`);
        continue;
      }
      if (!line.trim()) { flush(); continue; }

      const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
      if (h) { flush(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }

      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); continue; }

      // 표: 머리줄 다음 줄이 |---|---| 구분줄일 때만
      if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
        flush();
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitRow(lines[i++]));
        i--;
        const th = head.map((c) => `<th>${inline(c)}</th>`).join("");
        const tb = rows.map((r) => `<tr>${head.map((_, k) => `<td>${inline(r[k] || "")}</td>`).join("")}</tr>`).join("");
        out.push(`<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`);
        continue;
      }

      if (/^\s*>/.test(line)) {
        flush();
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
        i--;
        out.push(`<blockquote>${render(q.join("\n"))}</blockquote>`);
        continue;
      }

      if (LIST.test(line)) {
        flush();
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(LIST);
          if (m) {
            items.push({ indent: m[1].replace(/\t/g, "    ").length, ordered: /\d/.test(m[2]), text: m[3] });
          } else if (lines[i].trim() && /^\s+/.test(lines[i]) && items.length) {
            items[items.length - 1].text += " " + lines[i].trim(); // 목록 항목이 다음 줄로 이어진 경우
          } else break;
          i++;
        }
        i--;
        out.push(list(items));
        continue;
      }

      para.push(line.trim());
    }
    flush();
    return out.join("\n");
  }

  window.renderMarkdown = render;
})();

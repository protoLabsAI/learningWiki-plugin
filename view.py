"""Wiki console view (four-rules compliant, see api.py). Vanilla JS + the DS
kit — no build step. Left pane: page list with tier dots + due badges. Right
pane: the page, rendered by a deliberately tiny markdown renderer (headings,
emphasis, code, lists, links, [[wikilinks]] navigate in-view). The view READS;
mutation stays with the tools + the tutor skill.

Responsive by CONTAINER query, not media query: the page lives in a resizable
panel (rail iframe, right sidebar, palette, dialog), so the breakpoint keys
off the panel's own inline size. Below 560px it collapses to a single-pane
list ⇄ reader flow (`.page-open` toggles which) with a back button and
touch-floor tap targets; wide layouts ignore the class entirely.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wiki</title>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--pl-color-bg);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans);font-size:13px}
  .wrap{display:flex;height:100vh;container-type:inline-size;container-name:wiki}
  .list{width:290px;min-width:220px;border-right:var(--pl-border-width) solid var(--pl-color-border);
    overflow-y:auto;padding:var(--pl-space-3)}
  .reader{flex:1;overflow-y:auto;padding:var(--pl-space-4) var(--pl-space-6)}
  .top{display:flex;align-items:baseline;gap:var(--pl-space-2);margin-bottom:var(--pl-space-3)}
  h1{font-size:15px;margin:0;color:var(--pl-color-accent)}
  .stats{font-size:11px;color:var(--pl-color-fg-muted)}
  .item{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:var(--pl-radius);cursor:pointer}
  .item:hover{background:var(--pl-color-bg-raised)}
  .item.on{background:var(--pl-color-bg-raised);outline:1px solid var(--pl-color-border)}
  .item .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .dot{width:8px;height:8px;border-radius:50%;flex:none}
  .dot.novice{background:var(--pl-color-status-warning,#d9a441)}
  .dot.frontier{background:var(--pl-color-status-info,#6f9bff)}
  .dot.fluent{background:var(--pl-color-status-success,#57b98a)}
  .due{font-size:10px;font-family:var(--pl-font-mono);color:var(--pl-color-status-warning,#d9a441)}
  .back{display:none;align-items:center;gap:6px;margin:0 0 var(--pl-space-3);
    background:var(--pl-color-bg-raised);color:var(--pl-color-fg);cursor:pointer;
    border:var(--pl-border-width) solid var(--pl-color-border);border-radius:var(--pl-radius);
    font:inherit;padding:8px 12px}
  .back:hover{border-color:var(--pl-color-accent)}
  .meta{display:flex;gap:var(--pl-space-3);align-items:center;flex-wrap:wrap;
    font-size:11px;color:var(--pl-color-fg-muted);margin:0 0 var(--pl-space-3)}
  .pill{border:1px solid var(--pl-color-border);border-radius:99px;padding:1px 8px}
  .md{max-width:72ch;line-height:1.65;font-size:13.5px}
  .md h2{font-size:16px;margin:1.2em 0 .4em}
  .md h3{font-size:14px;margin:1em 0 .3em}
  .md code{font-family:var(--pl-font-mono);font-size:12px;background:var(--pl-color-bg-raised);
    border:1px solid var(--pl-color-border);border-radius:4px;padding:0 4px}
  .md pre{background:var(--pl-color-bg-raised);border:1px solid var(--pl-color-border);
    border-radius:var(--pl-radius);padding:10px;overflow-x:auto}
  .md pre code{border:none;background:none;padding:0}
  .md a,.wl{color:var(--pl-color-accent);cursor:pointer;text-decoration:underline}
  .links{margin-top:var(--pl-space-4);border-top:1px solid var(--pl-color-border);padding-top:var(--pl-space-3)}
  .links .h{text-transform:uppercase;letter-spacing:.06em;font-size:10.5px;
    color:var(--pl-color-fg-muted);margin-bottom:6px}
  .empty{color:var(--pl-color-fg-muted);padding:var(--pl-space-4)}

  /* Narrow PANEL (not viewport): single-pane list ⇄ reader. The container is
     .wrap itself, so only descendants are styled here (spec: a container's own
     size query can't restyle the container). */
  @container wiki (max-width: 560px){
    .list{width:100%;min-width:0;border-right:none}
    .reader{display:none;padding:var(--pl-space-3) var(--pl-space-4)}
    .page-open .list{display:none}
    .page-open .reader{display:block}
    .back{display:inline-flex}
    /* ADR 0086 touch floor: comfortable tap targets on phone-width panels. */
    .item{padding:11px 10px}
  }
</style>
<script>
  // RULE 3 — slug-aware base ("" on host, "/agents/<slug>" through the fleet proxy).
  var BASE = location.pathname.split("/plugins/")[0];
  // RULE 4 — DS kit CSS off BASE so the console theme applies live.
  (function(){ var l=document.createElement("link"); l.rel="stylesheet";
    l.href=BASE+"/_ds/plugin-kit.css"; document.head.appendChild(l); })();
</script>
</head><body>
<div class="wrap" id="wrap">
  <div class="list">
    <div class="top"><h1>Wiki</h1><span class="stats" id="stats"></span></div>
    <div id="err" class="pl-callout pl-callout--error" hidden></div>
    <div id="items"></div>
  </div>
  <div class="reader">
    <button class="back" data-back type="button">← Pages</button>
    <div id="page" class="empty">Select a page — or ask the agent to teach you something.</div>
  </div>
</div>
<script type="module">
  // RULE 4 — plugin-kit.js is an ES module → dynamic import, with a tokenless shim fallback.
  let kit;
  try { kit = await import(BASE + "/_ds/plugin-kit.js"); }
  catch (e) { kit = { initPluginView(){}, apiFetch: (p, i) => fetch(BASE + p, i) }; }

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

  // Tiny markdown renderer — enough for wiki pages; escapes first, renders second.
  function md(src){
    const lines = esc(src).split("\n");
    let out = [], inCode = false, inList = false;
    const inline = (t) => t
      .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, (_, tgt, lab) => `<span class="wl" data-wl="${tgt.trim()}">${lab}</span>`)
      .replace(/\[\[([^\]]+)\]\]/g, (_, tgt) => `<span class="wl" data-wl="${tgt.trim()}">${tgt}</span>`)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\*([^*]+)\*/g, "<i>$1</i>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    for (const line of lines){
      if (line.startsWith("```")){ out.push(inCode ? "</code></pre>" : "<pre><code>"); inCode = !inCode; continue; }
      if (inCode){ out.push(line); continue; }
      const li = line.match(/^\s*[-*]\s+(.*)$/);
      if (li){ if (!inList){ out.push("<ul>"); inList = true; } out.push("<li>" + inline(li[1]) + "</li>"); continue; }
      if (inList){ out.push("</ul>"); inList = false; }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h){ out.push(`<h${h[1].length + 1}>` + inline(h[2]) + `</h${h[1].length + 1}>`); continue; }
      if (line.trim() === ""){ out.push(""); continue; }
      out.push("<p>" + inline(line) + "</p>");
    }
    if (inList) out.push("</ul>");
    if (inCode) out.push("</code></pre>");
    return out.join("\n");
  }

  async function api(path){
    // RULES 2+3 — gated data via the kit's slug-aware authed fetch.
    const r = await kit.apiFetch(path);
    if (!r.ok) throw new Error(path + " -> " + r.status);
    return r.json();
  }

  let current = null;
  async function loadList(){
    try {
      const [pg, st] = await Promise.all([api("/api/plugins/learning_wiki/pages"), api("/api/plugins/learning_wiki/stats")]);
      $("stats").textContent = `${st.pages} pages · ${st.due} due`;
      $("items").innerHTML = pg.pages.map(p =>
        `<div class="item${p.slug === current ? " on" : ""}" data-slug="${esc(p.slug)}">
          <span class="dot ${p.tier}" title="${p.tier}"></span>
          <span class="t">${esc(p.title)}</span>
          ${p.due_cards ? `<span class="due">${p.due_cards} due</span>` : ""}
        </div>`).join("") || `<div class="empty">No pages yet.</div>`;
      $("err").hidden = true;
    } catch (e) { $("err").hidden = false; $("err").textContent = "" + e; }
  }

  async function openPage(slug){
    current = slug;
    try {
      const { page } = await api("/api/plugins/learning_wiki/pages/" + encodeURIComponent(slug));
      const mis = (page.misconceptions || []).filter(m => m.status === "open");
      const linkRow = (l) => `<span class="pill"><span class="wl" data-wl="${esc(l.slug)}">${esc(l.title)}</span> · ${esc(l.rel)}</span>`;
      $("page").className = "";
      $("page").innerHTML = `
        <div class="meta">
          <span class="pill">${esc(page.kind)}</span>
          <span class="pill"><span class="dot ${page.tier}" style="display:inline-block"></span> ${page.tier} · strength ${(page.strength).toFixed(2)}</span>
          ${mis.length ? `<span class="pill">⚠ ${mis.length} open misconception(s)</span>` : ""}
          <span>updated ${esc((page.updated_at || "").slice(0, 10))}</span>
        </div>
        <div class="md"><h2>${esc(page.title)}</h2>${md(page.content_md || "*stub — nothing filed yet*")}</div>
        <div class="links">
          ${page.links.length ? `<div class="h">Links</div>` + page.links.map(linkRow).join(" ") : ""}
          ${page.backlinks.length ? `<div class="h" style="margin-top:8px">Referenced by</div>` + page.backlinks.map(linkRow).join(" ") : ""}
        </div>`;
      $("wrap").classList.add("page-open");            // narrow panel: reader takes over
      document.querySelector(".reader").scrollTop = 0;
      loadList();
    } catch (e) { $("err").hidden = false; $("err").textContent = "" + e; }
  }

  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-back]")) { $("wrap").classList.remove("page-open"); return; }
    const wl = ev.target.closest("[data-wl]");
    if (wl) { openPage(wl.dataset.wl.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")); return; }
    const item = ev.target.closest(".item[data-slug]");
    if (item) openPage(item.dataset.slug);
  });

  let booted = false;
  function boot(){ if (booted) return; booted = true; loadList(); }
  kit.initPluginView(boot);
  setTimeout(boot, 800);
</script>
</body></html>
"""

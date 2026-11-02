"use strict";
// Single-page front end. All model and player text is inserted with textContent
// (via h()), never innerHTML: the agent's output is attacker-influenced by design.

const app = document.getElementById("app");
let ME = null;          // {user, models}
let pollTimer = null;

// ---------------------------------------------------------------- helpers

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && !path.startsWith("/api/login")) {
    ME = null; route(); throw new Error("sign in first");
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;
}

const ago = (t) => {
  if (!t) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};
const secs = (a) => (a.finished && a.started ? `${Math.round(a.finished - a.started)} s` : "");
const pad2 = (n) => String(n).padStart(2, "0");
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

function statusPill(a) {
  if (a.status === "queued") return h("span", { class: "pill warn" }, "queued");
  if (a.status === "running") return h("span", { class: "pill warn" }, "running");
  if (a.status === "error") return h("span", { class: "pill" }, "error");
  return a.passed ? h("span", { class: "pill ok" }, "flag") : h("span", { class: "pill bad" }, "no flag");
}

function setNav(name) {
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
}

// ---------------------------------------------------------------- router

async function route() {
  clearTimeout(pollTimer);
  if (!ME) {
    try { ME = await api("/api/me"); } catch (e) { app.replaceChildren(h("p", { class: "error-text" }, e.message)); return; }
  }
  const signedIn = !!ME.user;
  document.getElementById("nav").hidden = !signedIn;
  document.getElementById("who").hidden = !signedIn;
  if (!signedIn) return renderAuth();
  document.getElementById("who-name").textContent = ME.user.username;
  document.getElementById("nav-admin").hidden = !ME.user.admin;

  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (parts[0] === "c" && parts[1]) return await renderChallenge(parts[1], parts[3] ? +parts[3] : null);
    if (parts[0] === "scoreboard") return await renderScoreboard();
    if (parts[0] === "admin" && ME.user.admin) return await renderAdmin();
    return await renderList();
  } catch (e) {
    app.replaceChildren(h("div", { class: "panel" }, h("p", { class: "error-text" }, e.message)));
  }
}
window.addEventListener("hashchange", route);
document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/logout", { body: {} });
  ME = null; location.hash = "#/"; route();
});

// ---------------------------------------------------------------- auth

function renderAuth() {
  let mode = "login";
  const err = h("p", { class: "error-text" });
  const user = h("input", { name: "username", autocomplete: "username", required: true });
  const pw = h("input", { name: "password", type: "password", autocomplete: "current-password" });
  const invite = h("input", { name: "invite", autocomplete: "off" });
  const inviteRow = h("div", {}, h("label", {}, "Invite code"), invite);
  const submit = h("button", { class: "primary", type: "submit" });
  const tabs = ["login", "register"].map((m) =>
    h("button", { type: "button", onclick: () => { mode = m; sync(); } }, m === "login" ? "Sign in" : "Create account"));
  function sync() {
    tabs.forEach((t, i) => t.classList.toggle("on", ["login", "register"][i] === mode));
    inviteRow.hidden = mode === "login" || !ME.invite_required;
    pw.autocomplete = mode === "login" ? "current-password" : "new-password";
    submit.textContent = mode === "login" ? "Sign in" : "Create account";
    err.textContent = "";
  }
  const form = h("form", {
    onsubmit: async (ev) => {
      ev.preventDefault(); submit.disabled = true; err.textContent = "";
      try {
        await api(mode === "login" ? "/api/login" : "/api/register",
          { body: { username: user.value, password: pw.value, invite_code: invite.value } });
        ME = null; route();
      } catch (e) { err.textContent = e.message; }
      submit.disabled = false;
    },
  },
    h("div", {}, h("label", {}, "Username"), user),
    h("div", {}, h("label", {}, "Password"), pw),
    inviteRow, err, submit);
  sync();
  app.replaceChildren(h("div", { class: "auth panel" },
    h("h1", {}, "Agent Injection Lab"),
    h("p", { class: "muted" }, "CTF challenges where you attack an AI agent that uses tools."),
    h("div", { class: "tabs" }, tabs), form));
  user.focus();
}

// ---------------------------------------------------------------- challenge list

async function renderList() {
  setNav("challenges");
  const list = await api("/api/challenges");
  const solved = list.filter((c) => c.solved).length;
  app.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Challenges"),
        h("p", { class: "muted" }, "CTF challenges where you attack an AI agent that uses tools. Each one is built from a task in a published agent security benchmark (AgentDojo or AgentHarm) and runs against a real language model. You give an input, the agent does its job with its tools, and a grader checks what actually happened. You only get the flag if your input changed what the agent did.")),
      h("span", { class: `pill ${solved === list.length && list.length ? "ok" : ""}` }, `${solved} / ${list.length} solved`)),
    h("div", { class: "grid" }, list.map((c) =>
      h("a", { class: `card${c.solved ? " solved" : ""}`, href: `#/c/${c.slug}` },
        h("div", { class: "num" }, `#${pad2(c.number)}`),
        h("div", { class: "title" }, c.title),
        h("dl", {},
          h("dt", {}, "You control"), h("dd", {}, c.control),
          h("dt", {}, "Goal"), h("dd", {}, c.goal)),
        h("div", { class: "meta" },
          c.type ? h("span", { class: "pill" }, c.type) : null,
          c.solved ? h("span", { class: "pill ok" }, "✓ solved") : null,
          h("span", { class: "spacer" }),
          h("span", { class: "muted small" },
            `${c.solves} solve${c.solves === 1 ? "" : "s"}${c.my_attempts ? ` · ${c.my_attempts} tries` : ""}`))))));
}

// ---------------------------------------------------------------- challenge page

function scenarioNode(text) {
  // TRYIT.md paragraphs; only `code` spans are formatted.
  return h("div", { class: "scenario" }, text.split(/\n\s*\n/).map((para) =>
    h("p", {}, para.replace(/\n/g, " ").split(/(`[^`]+`)/).map((s) =>
      s.startsWith("`") && s.endsWith("`") ? h("code", {}, s.slice(1, -1)) : s))));
}

function flagBox(flag) {
  const btn = h("button", { class: "link", onclick: () => { navigator.clipboard?.writeText(flag); btn.textContent = "copied"; } }, "copy");
  return h("div", { class: "flagbox" }, h("span", {}, flag), btn);
}

async function renderChallenge(slug, openAttempt) {
  setNav("challenges");
  const [c, past] = await Promise.all([api(`/api/challenges/${slug}`), api(`/api/challenges/${slug}/attempts`)]);
  const draftKey = `asc-draft-${slug}`;

  const ta = h("textarea", { class: "code", spellcheck: false, placeholder: "Type the text you control…" });
  ta.value = store.get(draftKey) || "";
  const count = h("span", { class: "count" });
  const model = h("select", { title: "Model the agent runs on" },
    (ME.models || []).map((m) => h("option", { value: m }, m)));
  model.value = store.get("asc-model") || (ME.models || [])[0];
  const runBtn = h("button", { class: "primary" }, "Run attack");
  const formErr = h("p", { class: "error-text" });
  const resultPanel = h("div", { class: "panel stack" });
  const historyBody = h("tbody");
  const solvedSlot = h("div");
  let current = null;

  function updateCount() {
    const n = ta.value.length;
    count.textContent = `${n} / ${c.max_input_chars}`;
    count.classList.toggle("over", n > c.max_input_chars);
    store.set(draftKey, ta.value);
  }
  ta.addEventListener("input", updateCount);
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runBtn.click(); });
  model.addEventListener("change", () => store.set("asc-model", model.value));
  updateCount();

  function renderSolved(flag) {
    solvedSlot.replaceChildren(flag
      ? h("div", { class: "panel stack" }, h("h3", {}, "Solved: your flag"), flagBox(flag))
      : "");
  }
  renderSolved(c.flag);

  function renderHistory(rows) {
    historyBody.replaceChildren(...(rows.length ? rows.map((a) =>
      h("tr", { class: `clickable${a.id === current ? " current" : ""}`, onclick: () => showAttempt(a.id, true) },
        h("td", { class: "mono" }, `#${a.id}`),
        h("td", {}, statusPill(a)),
        h("td", { class: "mono" }, a.model),
        h("td", { class: "preview mono", title: a.input_preview }, a.input_preview),
        h("td", { class: "muted" }, ago(a.created)))) :
      [h("tr", {}, h("td", { colspan: 5, class: "muted" }, "No attempts yet."))]));
  }
  renderHistory(past);

  async function refreshHistory() { renderHistory(await api(`/api/challenges/${slug}/attempts`)); }

  async function showAttempt(id, fromHistory) {
    clearTimeout(pollTimer);
    current = id;
    if (fromHistory) window.history.replaceState(null, "", `#/c/${slug}/a/${id}`);
    let a;
    try { a = await api(`/api/attempts/${id}`); } catch (e) { resultPanel.replaceChildren(h("p", { class: "error-text" }, e.message)); return; }
    renderResult(resultPanel, a, c, {
      onLoadInput: () => { ta.value = a.input; updateCount(); ta.focus(); },
    });
    const live = a.status === "queued" || a.status === "running";
    runBtn.disabled = live;
    if (live) pollTimer = setTimeout(() => showAttempt(id), 2000);
    else {
      await refreshHistory();
      if (a.passed && a.flag) renderSolved(a.flag);
    }
  }

  runBtn.addEventListener("click", async () => {
    formErr.textContent = "";
    runBtn.disabled = true;
    try {
      const { id } = await api(`/api/challenges/${slug}/attempts`, { body: { text: ta.value, model: model.value } });
      window.history.replaceState(null, "", `#/c/${slug}/a/${id}`);
      await refreshHistory();
      await showAttempt(id);
    } catch (e) {
      formErr.textContent = e.message;
      runBtn.disabled = false;
    }
  });

  resultPanel.replaceChildren(
    h("h2", {}, "Result"),
    h("p", { class: "muted" }, "The model is random, so the same input can work once and fail the next time. Look at the trace and try again."));

  app.replaceChildren(
    h("a", { class: "crumb", href: "#/" }, "← All challenges"),
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "muted mono" }, `#${pad2(c.number)} · ${c.slug}`),
        h("h1", {}, c.title)),
      c.type ? h("div", {}, h("span", { class: "pill" }, c.type)) : null),
    h("div", { class: "chal-layout" },
      h("div", { class: "stack" },
        h("div", { class: "panel stack" },
          h("h3", {}, "Scenario"), scenarioNode(c.scenario || c.goal),
          h("h3", {}, "Briefing"), h("pre", { class: "briefing" }, c.briefing)),
        solvedSlot),
      h("div", { class: "stack" },
        h("div", { class: "panel" },
          h("label", {}, c.input_label), ta,
          h("div", { class: "input-foot" },
            runBtn,
            h("label", { class: "inline" }, "Model "), model,
            count),
          formErr),
        resultPanel,
        h("div", { class: "panel" },
          h("h2", {}, "Your attempts"),
          h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, ["#", "Result", "Model", "Input", "When"].map((t) => h("th", {}, t)))),
            historyBody))))));

  const resume = openAttempt || past.find((a) => a.status === "queued" || a.status === "running")?.id;
  if (resume) showAttempt(resume);
}

// ---------------------------------------------------------------- result view (shared with admin)

function renderResult(panel, a, c, { onLoadInput, admin } = {}) {
  const kids = [h("h2", {}, `Attempt #${a.id}`)];

  if (a.status === "queued" || a.status === "running") {
    const waited = Math.round(Date.now() / 1000 - (a.started || a.created));
    kids.push(h("div", { class: "running" }, h("span", { class: "spinner" }),
      h("div", {},
        h("b", {}, a.status === "queued" ? `Waiting in line (position ${a.queue_position || "?"})` : "The agent is working…"),
        h("div", { class: "muted" }, a.status === "running"
          ? `${waited} s so far on ${a.model}. A run can take a few minutes when the shared model is busy.`
          : "Only a few runs go to the model at once so the shared API key isn't rate-limited."))));
    if (a.progress && a.progress.length) {
      kids.push(h("h3", {}, "Tool calls so far"), h("ol", { class: "trace" },
        a.progress.map((p) => h("li", { class: "call" }, h("div", { class: "call-line" },
          h("span", { class: "fn" }, p.name), h("span", { class: "args" }, `(${JSON.stringify(p.args)})`),
          p.error ? h("span", { class: "pill bad" }, " error") : null))),
        h("p", { class: "muted" }, "Full tool results appear when the run finishes.")));
    }
    panel.replaceChildren(...kids);
    return;
  }

  if (a.status === "error") {
    kids.push(h("div", { class: "verdict warn" }, "The agent could not run",
      h("div", { class: "sub" }, a.error || "unknown error"),
      h("div", { class: "sub" }, "This is an infrastructure problem, not your answer. Try again in a minute.")));
  } else {
    // The verdict text is the challenge's own `run` output (web/challenges.json).
    kids.push(h("div", { class: `verdict ${a.passed ? "ok" : "bad"}` },
      a.message || (a.passed ? "Flag" : "No flag")));
    if (a.passed && a.flag) kids.push(flagBox(a.flag));
  }

  const r = a.result;
  if (r && r.partial) {
    // A failed run: show what it got through before failing (admins also get raw responses).
    const tools = r.tool_calls || [];
    kids.push(h("h3", {}, `Before it failed: ${(r.model_calls || []).length} model calls, ${tools.length} tool calls`));
    if (tools.length) {
      kids.push(traceNode({ trace: tools.flatMap((t) => [
        { kind: "tool_call", name: t.name, args: t.args },
        { kind: "tool_result", name: t.name, content: t.result, error: t.error }]) }));
    }
    if (admin) kids.push(adminDetails(a, r));
  } else if (r) {
    const facts = h("dl", { class: "kv" },
      h("dt", {}, "Model"), h("dd", { class: "mono" }, r.served_model || a.model),
      h("dt", {}, "Model calls"), h("dd", {}, `${r.steps_used}${r.hit_step_limit ? " (hit the step limit)" : ""}`),
      h("dt", {}, "Time"), h("dd", {}, secs(a)));
    if (r.tokens) {
      facts.append(h("dt", {}, "Tokens"), h("dd", {},
        `${r.tokens.input.toLocaleString()} in · ${r.tokens.output.toLocaleString()} out` +
        (r.tokens.reasoning ? ` (${r.tokens.reasoning.toLocaleString()} reasoning)` : "")));
    }
    if (c && c.user_task_label && r.user_task_done != null) {
      facts.append(h("dt", {}, c.user_task_label), h("dd", {}, r.user_task_done ? "yes" : "no"));
    }
    kids.push(facts);
    if (r.criteria) {
      kids.push(h("h3", {}, "Grader breakdown"), h("dl", { class: "kv" },
        Object.entries(r.criteria).map(([k, v]) => [
          h("dt", {}, h("span", { class: `pill ${v ? "ok" : "bad"}` }, v ? "pass" : "fail")),
          h("dd", { class: "mono" }, k)])));
    }
    kids.push(h("h3", {}, "What the agent did"), traceNode(r));
    if (admin) kids.push(adminDetails(a, r));
  }

  const tools = h("div", { class: "input-foot" });
  if (onLoadInput && a.input != null) tools.append(h("button", { onclick: onLoadInput }, "Load this input into the editor"));
  kids.push(h("details", {}, h("summary", { class: "muted" }, `Your input (${(a.input || "").length} chars)`),
    h("pre", { class: "briefing" }, a.input || "")), tools);
  panel.replaceChildren(...kids);
}

// Admin only: everything kept for investigations. Raw model responses include any
// reasoning text, which players don't see.
function adminDetails(a, r) {
  const calls = r.model_calls || [];
  return h("div", { class: "stack" },
    h("h3", {}, "For investigation"),
    h("dl", { class: "kv" },
      h("dt", {}, "Challenge version"), h("dd", { class: "mono" }, a.challenge_version || "–"),
      h("dt", {}, "Server commit"), h("dd", { class: "mono" }, (a.settings && a.settings.server_commit) || "–"),
      h("dt", {}, "Queued for"), h("dd", {}, a.started ? `${Math.round(a.started - a.created)} s` : "–"),
      h("dt", {}, "Tokens"), h("dd", {}, r.tokens ? `${r.tokens.input} in / ${r.tokens.output} out (${r.tokens.reasoning} reasoning)` : "–")),
    h("details", {}, h("summary", { class: "muted" }, "Run settings"),
      h("pre", { class: "briefing" }, JSON.stringify(a.settings, null, 2))),
    h("details", {}, h("summary", { class: "muted" }, `Raw model responses (${calls.length})`),
      calls.map((m, i) => h("div", {},
        h("p", { class: "muted small" },
          m.error ? `Call ${i + 1} · ${m.seconds} s · FAILED` :
          `Call ${i + 1} · ${m.seconds} s · ${m.finish_reason || ""} · ` +
          `${(m.usage && m.usage.prompt_tokens) || 0} in / ${(m.usage && m.usage.completion_tokens) || 0} out`),
        h("pre", { class: m.error ? "briefing err" : "briefing" }, m.error || JSON.stringify(m.message, null, 2))))));
}

function traceNode(r) {
  const items = [];
  const steps = r.trace || [];
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    if (s.kind === "tool_call") {
      const res = steps[i + 1] && steps[i + 1].kind === "tool_result" ? steps[++i] : null;
      const body = res ? (res.error || res.content || "(empty)") : null;
      items.push(h("li", { class: "call" },
        h("div", { class: "call-line" }, h("span", { class: "fn" }, s.name),
          h("span", { class: "args" }, `(${JSON.stringify(s.args)})`)),
        res ? h("details", { open: body.length < 400 },
          h("summary", {}, res.error ? "error" : `result · ${body.length} chars`),
          h("pre", { class: res.error ? "err" : "" }, body)) : null));
    } else if (s.kind === "assistant") {
      items.push(h("li", { class: "agent" }, h("div", { class: "agent-text" }, s.content)));
    }
  }
  if (r.hit_step_limit) items.push(h("li", {}, h("span", { class: "muted" }, `stopped: step limit (${r.steps_used}) reached`)));
  if (!items.length) items.push(h("li", {}, h("span", { class: "muted" }, "The agent made no tool calls.")));
  return h("ol", { class: "trace" }, items);
}

// ---------------------------------------------------------------- scoreboard

async function renderScoreboard() {
  setNav("scoreboard");
  const [rows, chals] = await Promise.all([api("/api/scoreboard"), api("/api/challenges")]);
  app.replaceChildren(
    h("div", { class: "page-head" }, h("h1", {}, "Scoreboard")),
    h("div", { class: "panel table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Player"), h("th", {}, "Solved"),
        chals.map((c) => h("th", { title: c.title }, pad2(c.number))), h("th", {}, "Attempts"), h("th", {}, "Last solve"))),
      h("tbody", {}, rows.map((r, i) => h("tr", {},
        h("td", { class: "mono" }, i + 1),
        h("td", {}, r.username, r.username === ME.user.username ? h("span", { class: "muted" }, " (you)") : ""),
        h("td", {}, h("b", {}, r.solved)),
        chals.map((c) => h("td", {}, r.challenges.includes(c.slug) ? h("span", { class: "pill ok" }, "✓") : h("span", { class: "muted" }, "·"))),
        h("td", { class: "muted" }, r.attempts),
        h("td", { class: "muted" }, ago(r.last))))))));
}

// ---------------------------------------------------------------- admin

async function renderAdmin() {
  setNav("admin");
  const [stats, chals, players] = await Promise.all([api("/api/admin/stats"), api("/api/challenges"), api("/api/admin/players")]);
  const dur = (sec) => sec == null ? "–" : sec < 120 ? `${Math.round(sec)} s` : sec < 7200 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;
  const byChal = Object.fromEntries(chals.map((c) => [c.slug, c]));
  const fChal = h("select", {}, h("option", { value: "" }, "All challenges"), chals.map((c) => h("option", { value: c.slug }, `${pad2(c.number)} ${c.title}`)));
  const fUser = h("input", { placeholder: "username" });
  const fPassed = h("select", {}, h("option", { value: "" }, "Any result"), h("option", { value: "1" }, "Flag"), h("option", { value: "0" }, "No flag"));
  const body = h("tbody");
  const detail = h("div", { class: "panel stack", hidden: true });

  async function load() {
    const p = new URLSearchParams({ challenge: fChal.value, username: fUser.value.trim(), passed: fPassed.value, limit: 200 });
    const rows = await api(`/api/admin/attempts?${p}`);
    body.replaceChildren(...rows.map((a) => h("tr", {
      class: "clickable", onclick: async () => {
        const full = await api(`/api/attempts/${a.id}`);
        detail.hidden = false;
        renderResult(detail, full, byChal[a.challenge], { admin: true });
        detail.prepend(h("p", { class: "muted" }, `${a.username} · ${a.challenge}`));
        detail.scrollIntoView({ behavior: "smooth" });
      },
    },
      h("td", { class: "mono" }, `#${a.id}`),
      h("td", {}, a.username),
      h("td", { class: "mono" }, a.challenge.slice(0, 2)),
      h("td", {}, statusPill(a)),
      h("td", { class: "mono" }, a.model),
      h("td", { class: "preview mono", title: a.input_preview }, a.input_preview),
      h("td", { class: "muted" }, secs(a)),
      h("td", { class: "muted" }, ago(a.created)))));
  }
  [fChal, fPassed].forEach((el) => el.addEventListener("change", load));
  fUser.addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });

  const s = stats.by_challenge_model;
  const total = s.reduce((n, r) => n + r.runs, 0), flags = s.reduce((n, r) => n + (r.flags || 0), 0);
  app.replaceChildren(
    h("div", { class: "page-head" }, h("h1", {}, "Admin"),
      h("div", { class: "filters" },
        h("a", { class: "btn", href: "/api/admin/export.jsonl" }, "Export attempts (JSONL)"),
        h("a", { class: "btn", href: "/api/admin/players.jsonl" }, "Export player progress (JSONL)"))),
    h("div", { class: "stat-row" },
      h("div", { class: "stat" }, h("b", {}, total), h("span", {}, "attempts")),
      h("div", { class: "stat" }, h("b", {}, flags), h("span", {}, "flags")),
      h("div", { class: "stat" }, h("b", {}, `${stats.running} / ${stats.max_concurrent}`), h("span", {}, "running now")),
      h("div", { class: "stat" }, h("b", {}, stats.queued), h("span", {}, "queued"))),
    h("div", { class: "panel table-wrap gap-below" },
      h("h2", {}, "Success rate by challenge and model"),
      h("table", {},
        h("thead", {}, h("tr", {}, ["Challenge", "Model", "Runs", "Flags", "Rate", "Errors", "Players", "Avg time", "Avg tokens in / out"].map((t) => h("th", {}, t)))),
        h("tbody", {}, s.map((r) => {
          const ok = r.runs - (r.errors || 0);
          return h("tr", {},
            h("td", {}, r.challenge), h("td", { class: "mono" }, r.model), h("td", {}, r.runs),
            h("td", {}, r.flags || 0), h("td", {}, ok ? `${Math.round(100 * (r.flags || 0) / ok)}%` : "–"),
            h("td", {}, r.errors || 0), h("td", {}, r.players),
            h("td", { class: "muted" }, r.avg_seconds ? `${Math.round(r.avg_seconds)} s` : "–"),
            h("td", { class: "muted" }, r.avg_tokens_in ? `${Math.round(r.avg_tokens_in).toLocaleString()} / ${Math.round(r.avg_tokens_out).toLocaleString()}` : "–"));
        })))),
    h("div", { class: "panel table-wrap gap-below" },
      h("h2", {}, "Player progress"),
      h("p", { class: "muted small" }, "Time to solve is wall-clock time from first opening the challenge to the first flag, not time actively spent."),
      h("table", {},
        h("thead", {}, h("tr", {}, ["Player", "Challenge", "First opened", "Visits", "Attempts", "Flags", "Attempts to solve", "Time to solve", "Tokens in / out"].map((t) => h("th", {}, t)))),
        h("tbody", {}, players.length ? players.map((p) => h("tr", {},
          h("td", {}, p.username), h("td", { class: "mono" }, p.challenge.slice(0, 2)),
          h("td", { class: "muted" }, ago(p.first_viewed)), h("td", {}, p.view_count),
          h("td", {}, p.attempts), h("td", {}, p.flags || 0),
          h("td", {}, p.attempts_to_solve ?? "–"), h("td", {}, dur(p.seconds_to_solve)),
          h("td", { class: "muted" }, p.tokens_in ? `${p.tokens_in.toLocaleString()} / ${p.tokens_out.toLocaleString()}` : "–"))) :
          [h("tr", {}, h("td", { colspan: 9, class: "muted" }, "No one has opened a challenge yet."))]))),
    h("div", { class: "panel" },
      h("h2", {}, "Recent attempts"),
      h("div", { class: "filters" }, fChal, fUser, fPassed, h("button", { onclick: load }, "Refresh")),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["#", "Player", "Ch", "Result", "Model", "Input", "Time", "When"].map((t) => h("th", {}, t)))),
        body))),
    h("div", { class: "gap-below" }), detail);
  await load();
}

route();

const $ = (sel) => document.querySelector(sel);

const themeEl = $("#theme");
const countryEl = $("#country");
const companyEl = $("#company");
const useLlmEl = $("#use-llm");
const statusEl = $("#status");
const resultsEl = $("#results");
const answerEl = $("#answer");
const usedEl = $("#used-sources");
const discardedEl = $("#discarded-sources");
const conflictsEl = $("#conflicts");
const gapsEl = $("#gaps");
const expertEl = $("#ask-expert");
const confValueEl = $("#confidence-value");
const ringValueEl = document.querySelector(".ring-value");
const docGridEl = $("#doc-grid");
const runBtn = $("#run-query");

const RING_LEN = 2 * Math.PI * 52;

function setStatus(msg, isError = false) {
  statusEl.textContent = msg || "";
  statusEl.classList.toggle("error", Boolean(isError));
}

function resetPipeline() {
  document.querySelectorAll(".pipe-step").forEach((el) => {
    el.classList.remove("active", "done");
  });
  document.querySelectorAll(".pipe-detail").forEach((el) => {
    el.textContent = "Waiting";
  });
}

async function animatePipeline(runFn) {
  const steps = ["filter", "relevance", "trust", "convert"];
  resetPipeline();
  for (const step of steps) {
    const el = document.querySelector(`.pipe-step[data-step="${step}"]`);
    el?.classList.add("active");
    const detail = document.querySelector(`[data-detail="${step}"]`);
    if (detail) detail.textContent = "Running…";
    await new Promise((r) => setTimeout(r, 180));
  }
  const result = await runFn();
  for (const step of steps) {
    const el = document.querySelector(`.pipe-step[data-step="${step}"]`);
    el?.classList.remove("active");
    el?.classList.add("done");
  }
  return result;
}

function applyPipelineDetails(debug) {
  if (!debug) {
    document.querySelector('[data-detail="filter"]').textContent = "Complete";
    document.querySelector('[data-detail="relevance"]').textContent = "Complete";
    document.querySelector('[data-detail="trust"]').textContent = "Complete";
    document.querySelector('[data-detail="convert"]').textContent = "Complete";
    return;
  }
  const nCand = debug.candidates?.length ?? 0;
  const nRel = debug.relevant?.length ?? 0;
  const nTrusted = debug.trusted?.length ?? 0;
  const nDisc = debug.discarded?.length ?? 0;
  document.querySelector('[data-detail="filter"]').textContent =
    `${nCand} candidate${nCand === 1 ? "" : "s"}`;
  document.querySelector('[data-detail="relevance"]').textContent =
    `${nRel} relevant`;
  document.querySelector('[data-detail="trust"]').textContent =
    `${nTrusted} trusted · ${nDisc} set aside`;
  document.querySelector('[data-detail="convert"]').textContent = "FOLDL merge done";
}

function setConfidence(score) {
  const pct = Math.round((score || 0) * 100);
  confValueEl.textContent = `${pct}%`;
  const offset = RING_LEN * (1 - Math.min(1, Math.max(0, score || 0)));
  // Force reflow then animate
  ringValueEl.style.strokeDasharray = `${RING_LEN}`;
  ringValueEl.style.strokeDashoffset = `${RING_LEN}`;
  requestAnimationFrame(() => {
    ringValueEl.style.strokeDashoffset = `${offset}`;
  });
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderAnswer(text) {
  // Light markdown-ish: ### headings and *italics*
  const lines = (text || "").split("\n");
  const html = lines
    .map((line) => {
      let s = escapeHtml(line);
      if (s.startsWith("### ")) {
        return `<h3>${s.slice(4)}</h3>`;
      }
      s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      return s || "<br />";
    })
    .join("\n");
  answerEl.innerHTML = html || "<p class='empty'>No answer produced.</p>";
}

function sourceCard({ title, score, why, reason }) {
  const pct = Math.round((score ?? 0) * 100);
  const body = why || reason || "";
  return `
    <article class="source-card">
      <div class="source-top">
        <span class="source-title">${escapeHtml(title || "Untitled")}</span>
        ${score != null ? `<span class="source-score">${pct}%</span>` : ""}
      </div>
      ${
        score != null
          ? `<div class="trust-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>`
          : ""
      }
      <p class="source-why">${escapeHtml(body)}</p>
    </article>
  `;
}

function renderResults(payload) {
  const summary = payload.summary || payload;
  const debug = payload.debug;

  resultsEl.classList.remove("hidden");
  setConfidence(summary.overall_confidence ?? 0);
  renderAnswer(summary.answer);
  applyPipelineDetails(debug);

  const used = summary.used_sources || [];
  usedEl.innerHTML = used.length
    ? used
        .map((s) =>
          sourceCard({
            title: s.title || s.doc_id,
            score: s.trust_score,
            why: s.why_trusted,
          })
        )
        .join("")
    : `<p class="empty">No trusted sources retained.</p>`;

  // Animate trust bars after paint
  requestAnimationFrame(() => {
    usedEl.querySelectorAll(".trust-bar > span").forEach((bar) => {
      const w = bar.style.width;
      bar.style.width = "0";
      requestAnimationFrame(() => {
        bar.style.width = w;
      });
    });
  });

  const discarded = summary.discarded_sources || [];
  discardedEl.innerHTML = discarded.length
    ? discarded
        .map((s) =>
          sourceCard({
            title: s.doc_id,
            reason: s.reason,
          })
        )
        .join("")
    : `<p class="empty">Nothing discarded.</p>`;

  const conflicts = summary.conflicts_detected || [];
  conflictsEl.innerHTML = conflicts.length
    ? conflicts
        .map(
          (c) => `
        <div class="conflict-item">
          <strong>${escapeHtml(c.claim || "Conflict")}</strong><br />
          ${escapeHtml(c.resolution || "")}
        </div>`
        )
        .join("")
    : `<p class="empty">No conflicts flagged.</p>`;

  const gaps = summary.gaps || [];
  gapsEl.innerHTML = gaps.length
    ? gaps.map((g) => `<div class="gap-item">${escapeHtml(g)}</div>`).join("")
    : "";

  const expert = summary.ask_expert;
  if (expert && expert.name) {
    expertEl.innerHTML = `
      <p class="expert-name">${escapeHtml(expert.name)}</p>
      <p class="expert-reason">${escapeHtml(expert.reason || "")}</p>
    `;
  } else {
    expertEl.innerHTML = `<p class="empty">No expert handoff suggested.</p>`;
  }
}

async function loadDocs() {
  try {
    const res = await fetch("/documents");
    if (!res.ok) throw new Error(await res.text());
    const docs = await res.json();
    if (!docs.length) {
      docGridEl.innerHTML = `<p class="empty">No documents yet. Click “Load demo docs”.</p>`;
      return;
    }
    docGridEl.innerHTML = docs
      .map((d) => {
        const countries = (d.country || [])
          .map((c) => `<span class="chip country">${escapeHtml(c)}</span>`)
          .join("");
        const companies = (d.company || [])
          .slice(0, 2)
          .map((c) => `<span class="chip">${escapeHtml(c)}</span>`)
          .join("");
        return `
          <article class="doc-card">
            <h3>${escapeHtml(d.title || d.doc_id)}</h3>
            <div class="doc-meta">${countries}${companies}</div>
            <p class="source-why">${escapeHtml(
              (d.keywords || []).slice(0, 6).join(" · ") || ""
            )}</p>
          </article>
        `;
      })
      .join("");
  } catch (err) {
    docGridEl.innerHTML = `<p class="empty error">Could not load documents: ${escapeHtml(
      err.message
    )}</p>`;
  }
}

async function runQuery() {
  const theme = themeEl.value.trim();
  if (!theme) {
    setStatus("Enter a question first.", true);
    themeEl.focus();
    return;
  }

  const filters = {};
  const country = countryEl.value.trim();
  const company = companyEl.value.trim();
  if (country) filters.country = country.split(/[\s,]+/).filter(Boolean);
  if (company) filters.company = [company];

  runBtn.disabled = true;
  setStatus("Running Filter → Relevance → Trust → Summary…");

  try {
    const payload = await animatePipeline(async () => {
      const res = await fetch("/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          theme,
          filters: Object.keys(filters).length ? filters : null,
          use_llm: useLlmEl.checked,
          debug: true,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || res.statusText);
      }
      return res.json();
    });
    renderResults(payload);
    setStatus("Pipeline complete.");
  } catch (err) {
    resetPipeline();
    setStatus(err.message || "Query failed", true);
  } finally {
    runBtn.disabled = false;
  }
}

async function seedDemo() {
  setStatus("Seeding demo corpus…");
  try {
    const apiKey = prompt("Enter API key (or leave empty for dev-key-change-in-production):");
    const headers = {};
    if (apiKey !== null) {
      headers["X-API-Key"] = apiKey || "dev-key-change-in-production";
    }
    const res = await fetch("/seed", { method: "POST", headers });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setStatus(`Loaded ${data.length} demo documents.`);
    await loadDocs();
  } catch (err) {
    setStatus(err.message || "Seed failed", true);
  }
}

function fillExample() {
  themeEl.value = "leave days for Acme employee in Belgium";
  countryEl.value = "BE";
  companyEl.value = "Acme NV";
  useLlmEl.checked = false;
  setStatus("Example loaded — run the trust pipeline.");
}

$("#run-query").addEventListener("click", runQuery);
$("#seed-demo").addEventListener("click", seedDemo);
$("#example").addEventListener("click", fillExample);
$("#refresh-docs").addEventListener("click", loadDocs);

themeEl.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runQuery();
});

loadDocs();

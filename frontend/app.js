/* HRX Loom capability demo - frontend.
   No external libraries and no CDN: this has to render in a meeting room with
   no network. The strip chart is hand-rolled SVG.

   Rendering rule that matters: a missing number renders as an em-dash, never as
   0 and never as 1.0. "No evidence" and "parity with llama.cpp" are different
   claims and the UI must not conflate them. */

const SKILLS = [
  ["loom",          "choose the workflow, apply shared operating rules"],
  ["loom-setup",    "install Loom, fetch catalog models, manage GPU/NPU servers"],
  ["loom-demo-ui",  "browser playground for chat, audio, images, embeddings"],
  ["loom-port-llm", "turn a GGUF into a runnable Loom package"],
  ["loom-roofline", "estimate the limit, choose targets and a budget"],
  ["loom-optimize", "improve speed while checking correctness"],
  ["loom-benchmark","validate, measure against llama.cpp, generate reports"],
  ["loom-improve",  "improve and evaluate the skills themselves"],
  ["loom-org",      "port, publish and maintain a collection on Hugging Face"],
];

let CATALOG = null;
let STATUS = null;

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* em-dash for anything absent - see rendering rule above. */
const fmt = (v, digits = 2, suffix = "") =>
  (v === null || v === undefined || Number.isNaN(v)) ? "—" : v.toFixed(digits) + suffix;

const gb = (bytes) =>
  (bytes === null || bytes === undefined) ? "—" : (bytes / 1e9).toFixed(1) + " GB";

const num = (v) =>
  (v === null || v === undefined) ? "—" : v.toLocaleString();

/* ---------------- tabs ---------------- */
$$(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.remove("is-active"));
    $$(".screen").forEach((x) => x.classList.remove("is-active"));
    t.classList.add("is-active");
    $("#screen-" + t.dataset.screen).classList.add("is-active");
    // The strip chart sizes to its container, which is zero-width while the
    // screen is display:none - so draw it only once the screen is visible.
    if (t.dataset.screen === "evidence") drawStrip();
  });
});

/* ---------------- boot ---------------- */
async function boot() {
  try {
    const [cRes, sRes] = await Promise.all([
      fetch("/api/catalog"),
      fetch("/api/status"),
    ]);
    CATALOG = await cRes.json();
    STATUS = await sRes.json();
  } catch (e) {
    $("#provText").textContent = "Could not reach the demo backend: " + e.message;
    return;
  }

  if (CATALOG.error) {
    $("#provText").textContent = CATALOG.error;
    return;
  }

  renderProvenance();
  renderSkills();
  renderDescriptor();
  renderStats();
  renderBoard();
  renderGates();
  drawStrip();

  $("#footMeta").textContent =
    `catalog published ${CATALOG.generated} · ${CATALOG.model_count} models · ` +
    `llama.cpp ROCm baseline`;
}

/* ---------------- provenance bar ---------------- */
function renderProvenance() {
  const bar = $("#provenanceBar");
  const live = STATUS.mode === "live";
  bar.classList.toggle("is-live", live);
  $("#provMode").textContent = live ? "live" : "replay";

  if (live) {
    $("#provText").innerHTML =
      `Connected to <code>${STATUS.loom_serve_url}</code> hosting ` +
      `<b>${(STATUS.server_models || []).join(", ") || "—"}</b>. ` +
      `Measurements you trigger are real readings from this box; ` +
      `catalog figures remain published AMD data.`;
    $("#liveBadge").textContent = "live";
    $("#liveBadge").classList.add("live");
    $("#probeBtn").disabled = false;
    $("#livePanelLede").innerHTML =
      `A <code>loom-serve</code> is answering on <code>${STATUS.loom_serve_url}</code>. ` +
      `Running a measurement sends real requests and reports what this machine did.`;
  } else {
    $("#provText").innerHTML =
      `No <code>loom-serve</code> on <code>${STATUS.loom_serve_url}</code>` +
      (STATUS.reason ? ` (${STATUS.reason})` : "") +
      `. Every figure shown is <b>published loom-fun data</b> from ` +
      `${CATALOG.generated} — nothing here was measured on this machine.`;
  }
}

/* ---------------- 01 descriptor ---------------- */
function renderDescriptor() {
  const sel = $("#descSelect");
  CATALOG.models.forEach((m, i) => {
    const o = document.createElement("option");
    o.value = i;
    o.textContent = m.name;
    sel.appendChild(o);
  });
  sel.addEventListener("change", () => paintDescriptor(CATALOG.models[sel.value]));
  paintDescriptor(CATALOG.models[0]);
}

function paintDescriptor(m) {
  $("#descModelName").textContent = m.name;
  const s = m.serve, c = m.source_checkpoint;
  const rows = [
    ["device", s.device || "—"],
    ["memory_bytes", s.memory_bytes ? `${num(s.memory_bytes)}  (${gb(s.memory_bytes)})` : "—"],
    ["context_tokens", num(s.context_tokens)],
    ["rows", num(s.rows)],
    ["max_tokens", num(s.max_tokens)],
    ["source GGUF", c.repo ? `${c.repo}` : "—"],
    ["quantization", c.quant || "—"],
    ["weights on disk", gb(c.size)],
    ["speculative drafter", m.has_drafter ? "yes" : "no"],
    ["vision projector", m.has_projector ? "yes" : "no"],
    ["endpoint", m.service.endpoint || "—"],
  ];
  $("#descTable").innerHTML = rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("");
}

/* ---------------- 02 skills ---------------- */
function renderSkills() {
  $("#skillList").innerHTML = SKILLS
    .map(([n, d]) => `<li><b>${n}</b><span>${d}</span></li>`)
    .join("");
}

/* ---------------- 03 stats ---------------- */
function renderStats() {
  const dec = CATALOG.spread.decode;
  const p32 = CATALOG.spread.prefill_32k;
  const withGeo = CATALOG.models.filter((m) => m.geomean_speedup !== null);
  const anySlow = CATALOG.models.filter((m) => m.regressions.length > 0).length;

  const stats = [
    [CATALOG.model_count, "models ported<br>18 architectures"],
    ["3–4", "days elapsed<br>for the whole experiment"],
    [p32 ? p32.median + "×" : "—", "median prefill @32k<br>vs llama.cpp ROCm"],
    [dec ? dec.median + "×" : "—", "median decode<br>vs llama.cpp ROCm"],
    [anySlow, "ports with ≥1 workload<br>still slower than llama.cpp"],
  ];
  $("#statRow").innerHTML = stats
    .map(([v, l]) => `<div class="stat"><div class="stat-v">${v}</div><div class="stat-l">${l}</div></div>`)
    .join("");

  $("#evidenceLede").innerHTML =
    `Published results from the loom-fun catalog (${CATALOG.generated}), measured on Strix Halo ` +
    `against llama.cpp ROCm. ${withGeo.length} of ${CATALOG.model_count} ports have a full ` +
    `geometric mean. Speedups above 1.0 favour Loom; below 1.0 llama.cpp is still faster — ` +
    `<b>both directions are shown</b>, because a benchmark page with no losses is not evidence.`;
}

/* ---------------- 03 strip chart ---------------- */
function drawStrip() {
  if (!CATALOG) return;
  const host = $("#stripChart");
  const W = Math.max(host.clientWidth || 860, 620);
  const rows = CATALOG.workloads;
  const rowH = 34, padT = 24, padB = 30, padL = 96, padR = 24;
  const H = padT + rows.length * rowH + padB;
  const plotW = W - padL - padR;

  // Log scale centred on parity. Clamp the domain so one outlier cannot
  // squash everything else into the middle.
  const LO = Math.log(0.5), HI = Math.log(3.5);
  const x = (v) => {
    const t = (Math.log(Math.max(0.5, Math.min(3.5, v))) - LO) / (HI - LO);
    return padL + t * plotW;
  };

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Speedup by workload">`;
  // Region where llama.cpp is faster.
  svg += `<rect class="band" x="${padL}" y="${padT - 8}" width="${x(1) - padL}" height="${rows.length * rowH + 8}"/>`;

  [0.5, 0.75, 1, 1.5, 2, 3].forEach((t) => {
    const px = x(t);
    svg += `<line class="${t === 1 ? "ax-par" : "ax-line"}" x1="${px}" y1="${padT - 8}" x2="${px}" y2="${padT + rows.length * rowH}"/>`;
    svg += `<text class="ax-txt" x="${px}" y="${H - 12}" text-anchor="middle">${t}×</text>`;
  });

  rows.forEach((w, i) => {
    const y = padT + i * rowH + rowH / 2;
    svg += `<text class="row-lbl" x="${padL - 10}" y="${y + 4}" text-anchor="end">${CATALOG.workload_labels[w]}</text>`;

    const vals = [];
    CATALOG.models.forEach((m) => {
      const sp = m.metrics[w].speedup;
      if (sp === null || sp === undefined) return;   // absent stays absent
      vals.push(sp);
      const slow = sp < 1.0;
      const jitter = ((m.name.length * 7 + i * 13) % 11) - 5;   // deterministic
      svg += `<circle class="dot${slow ? " slow" : ""}" cx="${x(sp).toFixed(1)}" cy="${(y + jitter * 0.9).toFixed(1)}" r="3.4"><title>${m.name} — ${sp}×</title></circle>`;
    });

    if (vals.length) {
      vals.sort((a, b) => a - b);
      const med = vals[Math.floor(vals.length / 2)];
      svg += `<line class="med-tick" x1="${x(med).toFixed(1)}" y1="${y - 13}" x2="${x(med).toFixed(1)}" y2="${y + 13}"><title>median ${med}×</title></line>`;
    }
  });

  svg += `</svg>`;
  host.innerHTML = svg;
}

window.addEventListener("resize", () => {
  if ($("#screen-evidence").classList.contains("is-active")) drawStrip();
});

/* ---------------- 03 leaderboard ---------------- */
function renderBoard() {
  const head = `<tr><th class="rk">#</th><th>model</th><th style="text-align:right">geomean</th>` +
               `<th style="text-align:right">p32k</th><th style="text-align:right">decode</th>` +
               `<th style="text-align:right">regr</th><th>evidence</th></tr>`;

  const rows = CATALOG.models.map((m, i) => {
    const geo = m.geomean_speedup;
    const p32 = m.metrics.prefill_32k.speedup;
    const dec = m.metrics.decode.speedup;
    const ev = m.evidence.url
      ? `<a href="${m.evidence.url}" target="_blank" rel="noopener">run ↗</a>`
      : "—";
    return `<tr>
      <td class="rk">${i + 1}</td>
      <td class="nm">${m.name}</td>
      <td class="sp${geo !== null && geo < 1 ? " slow" : ""}">${fmt(geo, 2, "×")}</td>
      <td class="sp${p32 !== null && p32 < 1 ? " slow" : ""}">${fmt(p32, 2, "×")}</td>
      <td class="sp${dec !== null && dec < 1 ? " slow" : ""}">${fmt(dec, 2, "×")}</td>
      <td class="rg">${m.regressions.length || ""}</td>
      <td class="ev">${ev}</td>
    </tr>`;
  }).join("");

  $("#board").innerHTML = head + rows;
}

/* ---------------- 04 gates ---------------- */
function renderGates() {
  const names = Object.keys(CATALOG.gate_labels);
  const counts = {};
  names.forEach((g) => (counts[g] = { pass: 0, "n/a": 0, unknown: 0, fail: 0, total: 0 }));

  CATALOG.models.forEach((m) => {
    Object.entries(m.gates.status || {}).forEach(([g, s]) => {
      if (!counts[g]) counts[g] = { pass: 0, "n/a": 0, unknown: 0, fail: 0, total: 0 };
      counts[g][s] = (counts[g][s] || 0) + 1;
      counts[g].total += 1;
    });
  });

  $("#gateGrid").innerHTML = names
    .filter((g) => counts[g].total > 0)
    .map((g) => {
      const c = counts[g], t = c.total;
      const pc = (n) => ((n / t) * 100).toFixed(1) + "%";
      return `<div class="gate">
        <div class="gate-nm">${CATALOG.gate_labels[g]}</div>
        <div class="gate-bar">
          <i class="g-pass" style="width:${pc(c.pass)}"></i>
          <i class="g-unk"  style="width:${pc(c.unknown + (c.fail || 0))}"></i>
          <i class="g-na"   style="width:${pc(c["n/a"])}"></i>
        </div>
        <div class="gate-ct">${c.pass} pass · ${c["n/a"]} n/a${c.unknown ? ` · ${c.unknown} unknown` : ""}${c.fail ? ` · ${c.fail} fail` : ""}</div>
      </div>`;
    }).join("");

  const t = CATALOG.gate_tally;
  const total = Object.values(t).reduce((a, b) => a + b, 0);
  $("#gateMeta").textContent = `${CATALOG.model_count} models · ${total} gate results`;
  $("#gateTally").innerHTML =
    `Across the catalog: <b>${t.pass || 0} pass</b>, ${t["n/a"] || 0} not applicable, ` +
    `${t.unknown || 0} unknown, <b>${t.fail || 0} fail</b>. ` +
    `"Not applicable" dominates where a model has no vision, no drafter or no thinking mode — ` +
    `it is not a silent failure, but it is also not evidence of correctness for a feature the ` +
    `model does not have.`;
}

/* ---------------- live probe ---------------- */
$("#probeBtn").addEventListener("click", async () => {
  const btn = $("#probeBtn");
  const out = $("#liveResult");
  btn.disabled = true;
  btn.textContent = "measuring…";
  out.innerHTML = `<p class="panel-lede" style="margin:0">Sending requests…</p>`;

  try {
    const r = await fetch("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        concurrency: +$("#pConc").value,
        prompt_tokens: +$("#pPrompt").value,
        max_tokens: +$("#pMax").value,
      }),
    });
    const d = await r.json();

    if (d.mode !== "live") {
      out.innerHTML = `<p class="live-err">Server not reachable${d.reason ? ` (${d.reason})` : ""}. ` +
                      `Nothing measured — staying in replay.</p>`;
    } else {
      out.innerHTML = `<table>
        <tr><td>model</td><td>${d.model}</td></tr>
        <tr><td>requests (concurrency)</td><td>${d.requests} (${d.concurrency} / cap ${d.max_workers})</td></tr>
        <tr><td>failed</td><td>${d.failed}</td></tr>
        <tr><td>wall time</td><td>${fmt(d.wall_seconds, 2, " s")}</td></tr>
        <tr><td>latency p50</td><td>${fmt(d.latency_p50, 3, " s")}</td></tr>
        <tr><td>latency p99</td><td>${fmt(d.latency_p99, 3, " s")}</td></tr>
        <tr><td>mean output rate</td><td>${d.mean_output_tok_s === null ? "—" : d.mean_output_tok_s + " tok/s"}</td></tr>
        <tr><td>aggregate output rate</td><td>${d.aggregate_output_tok_s === null ? "—" : d.aggregate_output_tok_s + " tok/s"}</td></tr>
      </table>
      <p class="caveat" style="margin-left:0;margin-right:0">Measured on this machine just now. ${d.measurement_note}</p>`;
    }
  } catch (e) {
    out.innerHTML = `<p class="live-err">${e.message}</p>`;
  } finally {
    btn.disabled = STATUS.mode !== "live";
    btn.textContent = "run measurement";
  }
});

boot();

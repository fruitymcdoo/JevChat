const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const thesaurus = document.getElementById("thesaurus");

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const pct = (x) => `${Math.round(x * 100)}%`;

function addMessage(role, text) {
  const wrap = el("div", `msg ${role}`);
  wrap.appendChild(el("div", "bubble", text));
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function renderBatch(batch) {
  const box = el("div", "batch");
  box.appendChild(el("div", "batch-head", `${batch.stage} — ${batch.latency_ms} ms`));
  for (const d of batch.decisions) {
    const row = el("div", "decision");
    const conf = d.confidence == null ? "" : ` (confidence ${d.confidence.toFixed(2)})`;
    row.appendChild(el("div", "q", `${d.id} → ${d.value_label}${conf}`));
    for (const p of d.probabilities) {
      const bar = el("div", "bar");
      const fill = el("span", "fill");
      fill.style.width = `${Math.round(p.p * 100)}%`;
      bar.appendChild(fill);
      bar.appendChild(el("span", "bar-label", `${(p.p * 100).toFixed(1)}%  ${p.label}`));
      row.appendChild(bar);
    }
    box.appendChild(row);
  }
  return box;
}

// One collapsible step per event. Big traces are only built when opened.
function addStep(trace, title, cls, batches) {
  const step = el("details", `step ${cls || ""}`);
  step.appendChild(el("summary", null, title));
  let built = false;
  step.addEventListener("toggle", () => {
    if (built || !step.open) return;
    built = true;
    for (const b of batches) step.appendChild(renderBatch(b));
  });
  trace.appendChild(step);
}

function handleEvent(ev, msg, trace) {
  const bubble = msg.querySelector(".bubble");
  if (ev.type === "plan") {
    const p = ev.plan;
    addStep(trace, `plan: ${p.move} · ${p.tone} · ${p.length} words → ${ev.sentences} sentence${ev.sentences === 1 ? "" : "s"} × ${ev.slots} slots`, "", ev.trace);
  } else if (ev.type === "draft") {
    msg.classList.remove("pending");
    bubble.textContent = ev.view || "…";
    bubble.classList.add("draft");
    const locked = ev.locked.map((l) => `${l.slot}=${l.token} (${pct(l.p)})`).join(", ");
    addStep(trace, `sentence ${ev.sentence}, round ${ev.round}: locked ${locked}`, "", ev.trace);
  } else if (ev.type === "settle") {
    bubble.textContent = ev.kept ? ev.text : ev.before;
    addStep(trace,
      `settle: "${ev.text}" ${pct(ev.score)} vs ${pct(ev.before_score)} before → ${ev.kept ? "kept" : "discarded"}`,
      ev.kept ? "" : "undo", ev.trace);
  } else if (ev.type === "assess") {
    bubble.textContent = ev.text;
    const s = ev.scores;
    addStep(trace,
      `${ev.accepted ? "✓ accept" : "✗ reject"} attempt ${ev.attempt}: responds ${pct(s.responds)} ` +
      `(grammatical ${pct(s.grammatical)}, complete ${pct(s.complete)}) vs ${pct(ev.threshold)}`,
      ev.accepted ? "" : "reject", ev.trace);
  } else if (ev.type === "reopen") {
    const slots = ev.slots.map((r) => `${r.slot}=${r.token} (${pct(r.p)})`).join(", ");
    addStep(trace, `↶ reopen shakiest slots: ${slots}`, "undo", []);
  } else if (ev.type === "done") {
    msg.classList.remove("pending");
    bubble.classList.remove("draft");
    bubble.textContent = ev.text;
    const tries = `${ev.attempts} attempt${ev.attempts === 1 ? "" : "s"}, ${ev.rounds} rounds`;
    const verdict = ev.accepted
      ? el("div", "verdict ok", `accepted at ${pct(ev.score)} · ${tries}`)
      : el("div", "verdict low", `best effort: ${pct(ev.score)}, below the ${pct(ev.threshold)} bar · ${tries}`);
    msg.insertBefore(verdict, trace);
    trace.querySelector("summary").textContent =
      `${ev.requests} requests · ${ev.latency_ms} ms · ${ev.input_tokens} tokens`;
  } else if (ev.type === "error") {
    msg.classList.remove("pending");
    msg.classList.add("error");
    bubble.textContent = ev.error;
  }
  log.scrollTop = log.scrollHeight;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addMessage("user", message);
  const msg = addMessage("assistant", "deciding…");
  msg.classList.add("pending");
  const trace = el("details", "trace");
  trace.appendChild(el("summary", null, "deciding…"));
  msg.appendChild(trace);
  form.querySelector("button").disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        thesaurus: thesaurus.checked,
        threshold: Number(document.getElementById("threshold").value) / 100,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      handleEvent({ type: "error", error: data.error || "Something went wrong." }, msg, trace);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) if (line.trim()) handleEvent(JSON.parse(line), msg, trace);
    }
  } catch (err) {
    handleEvent({ type: "error", error: "Could not reach the server." }, msg, trace);
  } finally {
    form.querySelector("button").disabled = false;
    input.focus();
  }
});

document.getElementById("reset").addEventListener("click", async () => {
  await fetch("/api/reset", { method: "POST" });
  log.innerHTML = "";
  input.focus();
});

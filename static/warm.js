import { element as $, format, metric, requestComparison, selectableRow } from "./ui.js";

let data = null;
let selected = "faascache";
let position = 0;
let running = false;

const names = {
  timeout: "Fixed timer",
  faascache: "FaasCache (paper rule)",
  adaptive: "Recent-demand variation",
  lru: "Most recent (LRU)",
  lfu: "Most frequent (LFU)",
};
const descriptions = {
  timeout: "Keep a container until its fixed idle timer expires. If memory fills sooner, remove the least recently used.",
  faascache: "Use the paper's frequency, startup cost, memory size, and logical clock to decide which idle container to remove. Adapted to an idle-memory budget.",
  lru: "Keep the containers used most recently.",
  lfu: "Keep containers whose functions have received the most requests so far.",
  adaptive: "Experimental: older requests gradually lose influence. Estimate near-term reuse and weigh the startup time saved against memory used.",
};

function updateEvent() {
  const events = data.runs[selected].demo_steps;
  if (!events.length) return;
  position = Math.max(0, Math.min(position, events.length - 1));
  const event = events[position];
  $("step").max = events.length - 1;
  $("step").value = position;
  $("step-count").textContent = `${position + 1} of ${events.length}`;
  $("prev").disabled = position === 0;
  $("next").disabled = position === events.length - 1;
  $("policy-badge").textContent = names[selected];
  const labels = {
    cold: "Create a container (cold start)",
    warm: "Reuse a container (warm start)",
    finished: "All done",
  };
  $("event-type").textContent = labels[event.kind] || event.kind;
  $("event-type").className = "pill";
  $("event-type").classList.toggle("cold", event.kind === "cold");
  $("event-type").classList.toggle("neutral", event.kind === "finished");
  $("event-title").textContent = event.title;
  $("event-text").textContent = `At ${format(event.time)} ms: ${event.message}`;
  $("memory-text").textContent = `${event.idle_mib} / ${data.budget_mib} MiB`;
  $("memory-bar").style.width = data.budget_mib
    ? `${Math.min(100, event.idle_mib / data.budget_mib * 100)}%` : "0%";
  $("decision-list").replaceChildren();
  $("memory-decisions").classList.toggle("hidden", !event.decisions.length);
  for (const decision of event.decisions) {
    const item = document.createElement("li");
    item.textContent = decision;
    $("decision-list").append(item);
  }
  $("chips").replaceChildren();
  if (!event.containers.length) {
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = "No containers are present yet.";
    $("chips").append(hint);
  }
  for (const container of event.containers) {
    const chip = document.createElement("span");
    chip.className = `chip ${container.state === "IDLE" ? "idle" : "busy"}`;
    const state = { IDLE: "ready", STARTING: "starting", BUSY: "working" };
    chip.textContent = `#${container.id} ${container.name} · ${state[container.state]}`;
    $("chips").append(chip);
  }
}

function render() {
  const runs = data.runs;
  const focus = runs[selected].summary;
  $("result-sub").textContent = `${data.trace.length} student requests · ${data.scenario_title}. Viewing: ${names[selected]}.`;
  $("scenario-story").textContent = data.scenario_description;
  $("insight").textContent = `FaasCache had ${runs.faascache.summary.cold_starts} cold starts; the recent-demand variation had ${runs.adaptive.summary.cold_starts}. This is a small illustration, not evidence of a general winner.`;
  $("p95-text").textContent = `${focus.p95_latency_ms} ms`;
  $("evictions-text").textContent = focus.evictions;
  $("startup-text").textContent = `${format(focus.startup_overhead_ms)} ms`;
  $("rule-description").textContent = descriptions[selected];
  $("highlight").replaceChildren(...[
    [`${focus.cold_starts} / ${focus.requests}`, "requests that needed a new container"],
    [`${focus.avg_latency_ms} ms`, "average response time"],
    [`${focus.avg_idle_mib} MiB`, "average ready memory"],
  ].map(([value, label]) => metric(value, label, "strong")));
  $("comparison").replaceChildren();
  for (const [key, run] of Object.entries(runs)) {
    if (!$("baselines").checked && ["lru", "lfu"].includes(key)) continue;
    const summary = run.summary;
    const row = selectableRow([
      names[key],
      `${summary.cold_starts} of ${summary.requests}`,
      `${summary.avg_latency_ms} ms`,
      `${summary.avg_idle_mib} MiB`,
    ], key === selected, () => select(key), `${names[key]}: ${summary.cold_starts} cold starts, ${summary.avg_latency_ms} milliseconds average response`);
    row.dataset.policy = key;
    row.title = descriptions[key];
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("i");
    fill.style.width = `${summary.cold_start_pct}%`;
    bar.append(fill);
    row.children[1].append(bar);
    $("comparison").append(row);
  }
  $("results").classList.remove("hidden");
  $("walkthrough").classList.remove("hidden");
  updateEvent();
}

function select(key) {
  selected = key;
  position = 0;
  render();
  $("comparison").querySelector(`[data-policy="${key}"]`).focus();
}

async function runComparison() {
  if (running) return;
  running = true;
  try {
    const result = await requestComparison(
      "/api/run",
      { scenario: $("scenario").value, budget_mib: Number($("budget").value) },
      [$("run"), $("scenario"), $("budget")],
      "Running...",
    );
    if (!result) return;
    data = result;
    position = 0;
    selected = "faascache";
    render();
  } finally {
    running = false;
  }
}

$("baselines").addEventListener("change", () => {
  if (!data) return;
  if (!$("baselines").checked && ["lru", "lfu"].includes(selected)) {
    selected = "faascache";
    position = 0;
  }
  render();
});
$("run").addEventListener("click", runComparison);
$("prev").addEventListener("click", () => { position--; updateEvent(); });
$("next").addEventListener("click", () => { position++; updateEvent(); });
$("step").addEventListener("input", (event) => {
  position = Number(event.target.value);
  updateEvent();
});
runComparison();

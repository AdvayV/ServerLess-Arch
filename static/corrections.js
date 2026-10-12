import { element as $, format, metric, requestComparison, selectableRow } from "./ui.js";

let data = null;
let selected = null;
let running = false;

async function runComparison() {
  if (running) return;
  running = true;
  try {
    const result = await requestComparison(
      "/api/corrections",
      { scenario: $("scenario").value },
      [$("run"), $("scenario")],
      "Simulating...",
    );
    if (!result) return;
    data = result;
    selected = Object.keys(data.runs)[0];
    render();
  } finally {
    running = false;
  }
}

function select(key) {
  selected = key;
  render();
  $("rows").querySelector(`[data-policy="${key}"]`).focus();
}

function render() {
  $("rows").replaceChildren();
  $("story").textContent = data.scenario_description;
  for (const [key, run] of Object.entries(data.runs)) {
    const summary = run.summary;
    const row = selectableRow([
      run.scheduler_name,
      run.cache_policy_name,
      summary.published,
      `${summary.deadline_misses} (${summary.deadline_miss_pct}%)`,
      `${summary.avg_completion_ms} ms`,
      summary.cache_hits,
      summary.cache_evictions,
    ], key === selected, () => select(key), `${run.scheduler_name}, ${run.cache_policy_name}`);
    row.dataset.policy = key;
    $("rows").append(row);
  }

  const run = data.runs[selected];
  const summary = run.summary;
  $("metrics").replaceChildren(...[
    [summary.tasks_created, "regrade jobs created"],
    [summary.discarded_stale, "running stale jobs discarded"],
    [summary.superseded_jobs, "queued jobs superseded"],
    [`${summary.avg_queue_wait_ms} ms`, "average queue wait"],
  ].map(([value, label]) => metric(value, label)));
  $("cache-note").textContent = [
    `${run.scheduler_name} / ${run.cache_policy_name}`,
    `${summary.cache_entries}/${summary.cache_capacity} components retained`,
    `${summary.cache_invalidations} invalidated`,
    `${summary.cache_hits} unchanged-component hits / ${summary.cache_misses} misses`,
    `${summary.cache_evictions} runtime evictions / ${summary.initial_cache_evictions} setup evictions`,
    `worker busy time ${summary.worker_busy_ms.map(format).join(" / ")} ms`,
  ].join(" · ");
  $("timeline").replaceChildren();
  for (const event of run.events) {
    const article = document.createElement("article");
    article.className = "event";
    article.classList.toggle("stale", ["stale", "superseded"].includes(event.kind));
    const tag = document.createElement("span");
    tag.className = "pill";
    tag.textContent = event.kind;
    const time = document.createElement("small");
    time.textContent = ` ${format(event.time)} ms`;
    const message = document.createElement("p");
    message.textContent = event.message;
    article.append(tag, time, message);
    $("timeline").append(article);
  }
  $("results").classList.remove("hidden");
}

$("run").addEventListener("click", runComparison);
$("scenario").addEventListener("change", runComparison);
runComparison();

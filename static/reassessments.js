import { element as $, format, metric, requestComparison, selectableRow } from "./ui.js";

const queueNames = { fifo: "FIFO", edf: "Earliest deadline", fair_edf: "Fair earliest deadline" };
const checkNames = { academic: "Academic", income: "Income", evidence: "Evidence" };
let data;
let selected = "edf_lru";
let running = false;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function runComparison() {
  if (running) return;
  running = true;
  try {
    const result = await requestComparison("/api/reassessments", {
      scenario: $("scenario").value,
      worker_count: Number($("workers").value),
      cache_capacity: Number($("capacity").value),
    }, [$("run"), $("scenario"), $("workers"), $("capacity")], "Computing cases...");
    if (!result) return;
    data = result;
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
  $("story").textContent = data.input.description;
  $("rows").replaceChildren();
  for (const [key, run] of Object.entries(data.runs)) {
    const s = run.summary;
    const row = selectableRow([
      queueNames[run.scheduler], run.strategy_name, `${s.published}/${s.requests}`,
      `${format(s.compute_ms)} ms`, `${format(s.avg_latency_ms)} ms`, s.deadline_misses, s.cache_hits,
    ], key === selected, () => select(key), `${queueNames[run.scheduler]}, ${run.strategy_name}`);
    row.dataset.policy = key;
    $("rows").append(row);
  }
  const run = data.runs[selected];
  const s = run.summary;
  const baseline = data.runs[`${run.scheduler}_full_recompute`].summary;
  const difference = baseline.compute_ms - s.compute_ms;
  $("insight").textContent = `${run.strategy_name} used ${format(s.compute_ms)} ms of computation: ` +
    `${difference >= 0 ? format(difference) + " ms less" : format(-difference) + " ms more"} than full recomputation under the same queue rule. ` +
    `${s.reference_verified_cases} published cases agree with the uncached reference evaluator.`;
  $("metrics").replaceChildren(...[
    [s.shared_subscriptions, "additional subscribers joined shared jobs"],
    [s.jobs_discarded_stale, "stale running jobs discarded"],
    [s.jobs_cancelled, "unneeded queued jobs cancelled"],
    [s.superseded_snapshots, "case generations superseded"],
  ].map(([value, label]) => metric(value, label)));
  $("cases").replaceChildren();
  for (const item of run.cases) {
    const card = node("article", undefined, "case");
    card.append(node("h3", `${item.student} / ${item.id}`));
    if (!item.result) {
      card.append(node("p", "Request cancelled", "status history"));
      $("cases").append(card);
      continue;
    }
    const passed = item.result.all_checks_passed;
    card.append(node("p", passed ? "All demo checks met - staff review required" : "Checks need attention - staff review required",
      `status${passed ? "" : " attention"}`));
    card.append(node("p", `Generation ${item.generation}; published at ${format(item.completion)} ms; ` +
      `${format(item.latency_ms)} ms after the original request. Deadline ${format(item.deadline)} ms${item.deadline_missed ? " (missed)" : " (met)"}.`, "note"));
    if (!item.current_at_end) card.append(node("p", "Historical result: a later update requires a new reassessment request.", "history"));
    const list = node("ul");
    for (const [name, result] of Object.entries(item.result.checks)) {
      const version = item.targets[name];
      list.append(node("li", `${checkNames[name]}: ${result.passed ? "pass" : "attention"}. ${result.explanation} ` +
        `Record v${version.record_version}, rule v${version.rule_version}.`));
    }
    card.append(list);
    $("cases").append(card);
  }
  $("resource-note").textContent = `Result cache: ${s.cache_entries}/${s.cache_capacity} slots; ` +
    `${s.cache_evictions} evictions, ${s.cache_invalidations} invalidations. ` +
    `Peak live case accumulators: ${s.peak_live_accumulator_components} delivered components, counted separately from cache storage. ` +
    `${s.carried_components} delivered components survived an amendment; stale execution consumed ${s.stale_compute_ms} ms.`;
  $("jobs").replaceChildren();
  for (const job of run.jobs) {
    const row = node("tr");
    for (const value of [job.id, job.key.student, checkNames[job.key.check],
      `${job.key.record_version} / ${job.key.rule_version}`, job.status,
      job.start === null ? "-" : `${job.start} ms`, `${job.completion} ms`]) row.append(node("td", value));
    $("jobs").append(row);
  }
  $("timeline").replaceChildren();
  for (const event of run.events) {
    const article = node("article", undefined, "event");
    article.append(node("small", `${format(event.time)} ms | ${event.kind}`), node("p", event.message));
    $("timeline").append(article);
  }
  $("results").classList.remove("hidden");
}

$("run").addEventListener("click", runComparison);
for (const id of ["scenario", "workers", "capacity"]) $(id).addEventListener("change", runComparison);
runComparison();

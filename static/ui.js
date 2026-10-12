// Small helpers shared by the two simulation interfaces.
export const element = (id) => document.getElementById(id);
export const format = (value) => Number(value).toLocaleString("en-US");

export function metric(value, label, tag = "b") {
  const card = document.createElement("div");
  card.className = "metric";
  const number = document.createElement(tag);
  number.textContent = value;
  const caption = document.createElement("span");
  caption.textContent = label;
  card.append(number, caption);
  return card;
}

export function selectableRow(values, selected, onSelect, label) {
  const row = document.createElement("tr");
  row.classList.toggle("selected", selected);
  row.tabIndex = 0;
  row.setAttribute("aria-label", label);
  for (const value of values) {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.append(cell);
  }
  row.addEventListener("click", onSelect);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  });
  return row;
}

export async function requestComparison(url, options, controls, busyLabel) {
  const button = element("run");
  const idleLabel = button.textContent;
  for (const control of controls) control.disabled = true;
  button.textContent = busyLabel;
  element("error").classList.add("hidden");
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : "Could not run the simulation");
    }
    return body;
  } catch (error) {
    element("error").textContent = error.message;
    element("error").classList.remove("hidden");
    return null;
  } finally {
    for (const control of controls) control.disabled = false;
    button.textContent = idleLabel;
  }
}

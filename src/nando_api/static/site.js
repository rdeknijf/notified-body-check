// Landing page: live check, feed URL builder, latest entries, exports.
"use strict";

const $ = (id) => document.getElementById(id);
const origin = window.location.origin;

async function getJSON(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => null);
  if (!response.ok && !body) throw new Error(`HTTP ${response.status}`);
  return body;
}

function fmtTime(iso) {
  if (!iso) return "unknown";
  return iso.replace("T", " ").replace(/(:\d\d)(\.\d+)?Z$/, "$1 UTC").replace(/:\d\d UTC$/, " UTC");
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

// --- facts line -------------------------------------------------------------

async function loadFacts() {
  try {
    const status = await getJSON("/v1/status");
    const counts = status.data.counts;
    const facts = $("facts");
    facts.replaceChildren(
      el("b", {}, counts.legislation.toLocaleString("en")), " acts, ",
      el("b", {}, counts.notifications_latest.toLocaleString("en")), " notifications (",
      el("b", {}, counts.notified.toLocaleString("en")), " currently notified), synced ",
      el("b", {}, fmtTime(status.as_of)), "."
    );
  } catch {
    $("facts").textContent = "The local register is not available right now.";
  }
}

// --- check -------------------------------------------------------------------

const STAMP = {
  Active: ["Notified", "active"],
  Suspended: ["Suspended", "adverse"],
  Withdrawn: ["Withdrawn", "adverse"],
  "Expired/Withdrawn": ["Expired/Withdrawn", "adverse"],
  Expired: ["Expired", "adverse"],
};

const REASONS = {
  invalid_body_number: "That is not a body number. Use four digits, like 0123.",
  body_not_found: "The register has no body with this number.",
  legislation_not_found: "No act matches this. Try a number like 2017/745 or 2014/68/EU.",
  legislation_ambiguous: "More than one act matches. Add the suffix, like 2014/68/EU.",
  no_notification: "This body has no notification under this act.",
  end_date_passed: "The end date has passed.",
};

function checkParams(form) {
  const params = new URLSearchParams();
  for (const name of ["body", "legislation", "procedure", "product"]) {
    const value = form.elements[name].value.trim();
    if (value) params.set(name, value);
  }
  return params;
}

function entry(list, term, value, opts = {}) {
  const dd = el("dd", opts.no ? { class: "no" } : {}, value ?? "none");
  if (opts.derived) dd.append(el("span", { class: "derived" }, opts.derived));
  list.append(el("dt", {}, term), dd);
}

function renderCheck(answer) {
  const data = answer.data;
  const card = $("card");
  const stamp = $("card-stamp");
  const entries = $("card-entries");
  entries.replaceChildren();
  $("card-number").textContent = data.body?.body_number ?? (data.query.body || "----");
  $("card-name").textContent = data.body
    ? `${data.body.name}, ${data.body.country}`
    : REASONS[data.reason] ?? "Not found.";
  $("card-act").textContent = data.legislation
    ? (data.legislation.name.includes(data.legislation.number ?? "\u0000")
        ? data.legislation.name
        : `${data.legislation.code}: ${data.legislation.name}`)
    : data.reason === "legislation_ambiguous" ? REASONS[data.reason] : "";

  let word = "Not found";
  let tone = "unknown";
  if (data.notified) [word, tone] = ["Notified", "active"];
  else if (data.reason === "end_date_passed") [word, tone] = ["End date passed", "adverse"];
  else if (data.status && STAMP[data.status]) [word, tone] = STAMP[data.status];
  else if (data.status) [word, tone] = [data.status, "adverse"];
  else if (data.reason === "no_notification") [word, tone] = ["Not notified", "adverse"];
  stamp.textContent = word;
  stamp.dataset.tone = tone;
  stamp.hidden = false;
  stamp.classList.remove("pressed");
  void stamp.offsetWidth;
  stamp.classList.add("pressed");

  if (data.notification_id) {
    entry(entries, "Notified", data.notified ? "yes" : "no", data.reason === "end_date_passed" ? { derived: REASONS.end_date_passed } : {});
    entry(entries, "Status", data.status, data.status_basis ? { derived: `${data.status_basis} (NANDO status id ${data.status_id})` } : {});
    entry(entries, "Since", data.since ? fmtTime(data.since) : null, { derived: "from stored versions" });
    entry(entries, "End date", data.end_date);
    entry(entries, "Approved", data.last_approval_date);
    if (data.procedure_in_scope !== null) {
      entry(entries, "Procedure", data.procedure_in_scope ? data.procedure_matches.join("; ") : "no matching entry", { derived: "text match on the scope; check the full notification" });
    }
    if (data.product_in_scope !== null) {
      entry(entries, "Product", data.product_in_scope ? data.product_matches.slice(0, 3).join("; ") : "no matching entry", { derived: "text match on the scope; check the full notification" });
    }
    entry(entries, "Notification", String(data.notification_id));
  }
  entry(entries, "As of", fmtTime(answer.as_of));
  card.setAttribute("aria-busy", "false");
}

let checkSeq = 0;

function clearCard(message) {
  $("card-number").textContent = "----";
  $("card-name").textContent = message;
  $("card-act").textContent = "";
  $("card-stamp").hidden = true;
  $("card-entries").replaceChildren();
  $("raw-json").textContent = "{}";
}

async function runCheck(params, { push = false } = {}) {
  const seq = ++checkSeq;
  const form = $("check-form");
  const button = form.querySelector("button[type=submit]");
  const path = `/v1/check?${params.toString()}`;
  $("curl-line").textContent = `curl '${origin}${path.replaceAll("%2F", "/")}'`;
  clearCard("Checking the local register\u2026");
  $("card").setAttribute("aria-busy", "true");
  button.setAttribute("aria-disabled", "true");
  try {
    const answer = await getJSON(path);
    if (seq !== checkSeq) return;
    $("raw-json").textContent = JSON.stringify(answer, null, 2);
    if (answer.error) {
      $("card-name").textContent = "Fill in a body number and an act.";
      $("card-stamp").hidden = true;
      $("card-entries").replaceChildren();
      $("card").setAttribute("aria-busy", "false");
    } else {
      renderCheck(answer);
    }
    if (push) history.replaceState(null, "", `?${params.toString().replaceAll("%2F", "/")}#check`);
  } catch {
    if (seq !== checkSeq) return;
    clearCard("The check failed. Try again in a moment.");
    $("card").setAttribute("aria-busy", "false");
  } finally {
    if (seq === checkSeq) button.removeAttribute("aria-disabled");
  }
}

function initCheck() {
  const form = $("check-form");
  const initial = new URLSearchParams(window.location.search);
  for (const name of ["body", "legislation", "procedure", "product"]) {
    if (initial.get(name)) form.elements[name].value = initial.get(name);
  }
  if (initial.get("procedure") || initial.get("product")) $("more").open = true;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runCheck(checkParams(form), { push: true });
  });
  for (const link of document.querySelectorAll("[data-example]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const params = new URL(link.href).searchParams;
      form.elements.body.value = params.get("body");
      form.elements.legislation.value = params.get("legislation");
      form.elements.procedure.value = "";
      form.elements.product.value = "";
      runCheck(checkParams(form), { push: true });
    });
  }
  runCheck(checkParams(form));
}

// --- acts, feed builder, latest entries ---------------------------------------

async function loadActs() {
  try {
    const acts = await getJSON("/v1/legislation");
    const select = $("feed-legislation");
    const list = $("acts");
    const active = acts.data.filter((a) => a.status === "Active").sort((a, b) => a.code.localeCompare(b.code));
    for (const act of active) {
      const value = act.number || String(act.legislation_id);
      select.append(el("option", { value }, `${act.code} (${act.active_notifications} notified)`));
      list.append(el("option", { value }, act.name));
    }
  } catch {
    /* the feed builder still works with "All acts" */
  }
}

function updateFeed() {
  const params = new URLSearchParams();
  const act = $("feed-legislation").value;
  const body = $("feed-body").value.trim();
  const type = $("feed-type").value;
  if (act) params.set("legislation", act);
  if (body) params.set("body", body);
  if (type) params.set("change_type", type);
  const query = params.toString();
  $("feed-url").textContent = `${origin}/v1/changes.atom${query ? `?${query.replaceAll("%2F", "/").replaceAll("%2C", ",")}` : ""}`;
}

const KIND = {
  new: "New", status_changed: "Status", suspended: "Suspended", withdrawn: "Withdrawn",
  restored: "Restored", end_date_changed: "End date", scope_added: "Scope +",
  scope_removed: "Scope -", body_details_changed: "Details", removed: "Removed",
};

async function loadLog() {
  const list = $("log-list");
  try {
    const feed = await getJSON("/v1/changes?order=desc&limit=8");
    if (!feed.data.length) {
      list.replaceChildren(el("li", {}, el("span", { class: "empty" },
        `The log is empty. The first sync (${fmtTime(feed.as_of)}) is the baseline, and changes appear from the next sync on.`)));
      return;
    }
    list.replaceChildren(...feed.data.map((change) => {
      const who = change.body_number ? `NB ${change.body_number}` : change.body_ref;
      const act = change.legislation_code ? ` under ${change.legislation_code}` : "";
      return el("li", {},
        el("time", { datetime: change.observed_at }, change.observed_at.slice(0, 10)),
        el("span", {}, el("span", { class: "kind" }, KIND[change.change_type] ?? change.change_type),
          `${who}${change.body_name ? ` (${change.body_name})` : ""}${act}`));
    }));
  } catch {
    list.replaceChildren(el("li", {}, el("span", { class: "empty" }, "The change log is not available right now.")));
  }
}

async function loadExports() {
  const list = $("export-list");
  try {
    const exports = await getJSON("/v1/exports");
    if (!exports.data.length) {
      list.replaceChildren(el("li", {}, "No export yet. The first one appears after the next nightly sync."));
      return;
    }
    list.replaceChildren(...exports.data.slice(0, 7).map((item) => el("li", {},
      el("span", {}, item.date),
      el("a", { href: item.jsonl }, "JSONL"),
      el("a", { href: item.csv }, "CSV"))));
  } catch {
    list.replaceChildren(el("li", {}, "The export list is not available right now."));
  }
}

// --- copy buttons ---------------------------------------------------------------

function initCopy() {
  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      const text = $(button.dataset.copy).textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
        $("copy-status").textContent = "Copied to the clipboard.";
      } catch {
        button.textContent = "Select it";
        $("copy-status").textContent = "Copy failed. Select the text and copy it by hand.";
      }
      setTimeout(() => { button.textContent = "Copy"; }, 1600);
    });
  }
}

$("feed-form").addEventListener("submit", (event) => event.preventDefault());
for (const id of ["feed-legislation", "feed-body", "feed-type"]) {
  $(id).addEventListener("input", updateFeed);
}
initCopy();
initCheck();
updateFeed();
loadFacts();
loadActs().then(updateFeed);
loadLog();
loadExports();

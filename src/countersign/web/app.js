/* Countersign console.
   One page, hash-routed, no framework. Every number on screen came from the API;
   nothing is computed here that the product did not already count. */

(() => {
  "use strict";

  const state = {
    tenant: null,
    view: "overview",
    param: null,
    tab: null,
    info: null,
    renders: [],
    cache: {},
  };

  // ------------------------------------------------------------ utilities

  const $ = (selector) => document.querySelector(selector);

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

  const day = (value) => (value ? String(value).slice(0, 10) : "–");

  const stamp = (value) => (value ? String(value).replace("T", " ").slice(0, 16) : "–");

  const OUTCOME_STATUS = {
    effective: "pos",
    ineffective: "neg",
    inconclusive: "crit",
    not_run: "neutral",
  };

  const OUTCOME_LABEL = {
    effective: "Effective",
    ineffective: "Not effective",
    inconclusive: "Inconclusive",
    not_run: "Not run",
  };

  const FINDING_LABEL = {
    open: "Open",
    risk_accepted: "Risk accepted",
    remediation_agreed: "Remediation agreed",
    closed: "Closed",
  };

  const FINDING_STATUS = {
    open: "neg",
    risk_accepted: "crit",
    remediation_agreed: "info",
    closed: "pos",
  };

  function toast(message, kind) {
    const node = document.createElement("div");
    node.className = "toast" + (kind ? ` is-${kind}` : "");
    node.textContent = message;
    $("#toast-region").append(node);
    setTimeout(() => node.remove(), 4200);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }

  const tenantUrl = (suffix) => `/api/tenants/${encodeURIComponent(state.tenant)}${suffix}`;

  // ------------------------------------------------------------- fragments

  const statusPill = (kind, label) => `<span class="status ${kind}">${esc(label)}</span>`;

  const severityPill = (severity) =>
    `<span class="status sev-${esc(severity)}">${esc(severity)}</span>`;

  const outcomePill = (outcome) =>
    statusPill(OUTCOME_STATUS[outcome] || "neutral", OUTCOME_LABEL[outcome] || outcome);

  function card(title, sub, body, actions) {
    return `<section class="card">
      <div class="card-head">
        <div><h2>${esc(title)}</h2>${sub ? `<p class="sub">${esc(sub)}</p>` : ""}</div>
        ${actions ? `<div class="header-actions">${actions}</div>` : ""}
      </div>
      ${body}
    </section>`;
  }

  const tile = (label, value, foot, kind) =>
    `<div class="tile${kind ? ` is-${kind}` : ""}">
      <span class="tile-label">${esc(label)}</span>
      <span class="tile-value">${esc(value)}</span>
      ${foot ? `<span class="tile-foot">${esc(foot)}</span>` : ""}
    </div>`;

  const empty = (message) => `<div class="empty">${esc(message)}</div>`;

  const evidenceChips = (refs) =>
    !refs || !refs.length
      ? '<span class="tiny">no evidence recorded</span>'
      : `<div class="chips">${refs
          .slice(0, 14)
          .map(
            (ref) =>
              `<span class="chip evidence" title="${esc(ref.label || "")} · digest ${esc(
                ref.digest || "none"
              )}">${esc(ref.source)}:${esc(ref.locator)}</span>`
          )
          .join("")}${
          refs.length > 14 ? `<span class="chip">+${refs.length - 14} more</span>` : ""
        }</div>`;

  // ------------------------------------------------------------- shell bar

  function renderShell() {
    const info = state.info;
    const select = $("#tenant-select");
    select.innerHTML = info.tenants
      .map(
        (t) =>
          `<option value="${esc(t.id)}"${t.id === state.tenant ? " selected" : ""}>${esc(
            t.display_name
          )}</option>`
      )
      .join("");

    const mode = $("#mode-pill");
    if (info.model_mode === "demo") {
      mode.hidden = true;
    } else {
      mode.hidden = false;
      mode.textContent = `Strands · ${info.model_mode}`;
      mode.className = "shell-pill is-live";
      mode.title = info.model_description;
    }

    const chain = $("#chain-pill");
    chain.textContent = info.audit.intact ? "Evidence chain intact" : "Chain broken";
    chain.className = "shell-pill " + (info.audit.intact ? "is-good" : "is-bad");
    chain.title = info.audit.detail;

    const button = $("#auth-button");
    const avatar = $("#avatar");
    if (info.user) {
      button.textContent = "Sign out";
      avatar.hidden = false;
      avatar.textContent = info.user.slice(0, 2).toUpperCase();
    } else {
      button.textContent = "Sign in";
      avatar.hidden = true;
    }

    $("#as-of-note").textContent = `Programme measured as at ${info.as_of}. ${
      info.model_mode === "demo"
        ? "Reports are composed deterministically; no model is invoked."
        : "Reports are written by the Strands agent graph."
    }`;

    const openFindings = info.summary.open_findings || 0;
    const badgeF = $("#badge-findings");
    badgeF.hidden = !openFindings;
    badgeF.textContent = openFindings;

    const proposed = (info.summary.controls && info.summary.controls.proposed) || 0;
    const badgeD = $("#badge-domains");
    badgeD.hidden = !proposed;
    badgeD.textContent = proposed;

    document.querySelectorAll(".nav-item").forEach((item) =>
      item.classList.toggle("is-active", item.dataset.view === state.view)
    );
  }

  // -------------------------------------------------------------- overview

  async function viewOverview() {
    const info = state.info;
    const summary = info.summary;
    const [findings, controls] = await Promise.all([
      api(tenantUrl("/findings")),
      api(tenantUrl("/controls")),
    ]);

    const scheduled = controls.filter((c) => c.status === "scheduled");
    const blocked = controls.filter((c) => c.status === "proposed" && !c.runnable);
    const open = findings.filter((f) => f.status === "open" || f.status === "remediation_agreed");
    const critical = open.filter((f) => f.severity === "critical" || f.severity === "high").length;
    const effective = (summary.runs && summary.runs.effective) || 0;
    const total = Object.values(summary.runs || {}).reduce((a, b) => a + b, 0);

    header(
      "Control programme",
      `${info.tenants.find((t) => t.id === state.tenant)?.display_name || state.tenant}`,
      "Controls run on their own schedule. What reaches this page is what a person has to decide.",
      info.user
        ? `<button class="button" data-action="run-due">Run everything due</button>
           <button class="button emphasized" data-action="seed">Re-run the programme</button>`
        : `<button class="button emphasized" data-action="signin">Sign in to act</button>`
    );

    const attention = open
      .slice(0, 6)
      .map(
        (f) => `<tr class="clickable" data-href="#finding/${f.id}">
          <td>${severityPill(f.severity)}</td>
          <td><span class="primary">${esc(f.title)}</span>
              <span class="secondary">${esc(f.control_code)} · ${esc(
          f.proposed_owner || "owner not assigned"
        )}</span></td>
          <td>${statusPill(FINDING_STATUS[f.status], FINDING_LABEL[f.status])}</td>
        </tr>`
      )
      .join("");

    const schedule = scheduled
      .slice()
      .sort((a, b) => String(a.next_due).localeCompare(String(b.next_due)))
      .map((c) => {
        const run = c.last_run;
        return `<tr class="clickable" data-href="#control/${encodeURIComponent(c.code)}">
          <td><span class="code">${esc(c.code)}</span></td>
          <td><span class="primary">${esc(c.title)}</span>
              <span class="secondary">${esc(c.domain_title)}</span></td>
          <td><span class="chip">${esc(c.periodicity)}</span></td>
          <td>${run ? outcomePill(run.outcome) : statusPill("neutral", "Never run")}</td>
          <td class="num">${run ? esc(run.population_size) : "–"}</td>
          <td class="num">${
            run
              ? run.exception_count
                ? `<strong style="color:var(--neg)">${run.exception_count}</strong>`
                : "0"
              : "–"
          }</td>
          <td>${esc(day(c.next_due))}</td>
        </tr>`;
      })
      .join("");

    $("#content").innerHTML = `
      <div class="grid kpi">
        ${tile("Open findings", open.length, `${critical} at high or critical`, open.length ? "neg" : "pos")}
        ${tile("Controls scheduled", scheduled.length, `${controls.length} proposed in total`)}
        ${tile(
          "Controls effective",
          total ? `${effective}/${total}` : "–",
          "on their most recent run"
        )}
        ${tile(
          "Not testable yet",
          blocked.length,
          blocked.length ? "no evidence source connected" : "every proposed control can run",
          blocked.length ? "crit" : "pos"
        )}
      </div>

      ${card(
        "Needs a person",
        open.length
          ? `${plural(open.length, "finding")} waiting on a decision`
          : "Nothing is waiting on a decision",
        open.length
          ? `<div class="card-body tight table-scroll"><table class="table">
              <thead><tr><th style="width:110px">Severity</th><th>Finding</th><th style="width:170px">Status</th></tr></thead>
              <tbody>${attention}</tbody></table></div>`
          : empty("Every finding raised has been dispositioned."),
        open.length > 6 ? `<a class="button ghost" href="#findings">See all ${open.length}</a>` : ""
      )}

      ${card(
        "The schedule",
        `${plural(scheduled.length, "control")} running automatically`,
        scheduled.length
          ? `<div class="card-body tight table-scroll"><table class="table">
              <thead><tr><th>Code</th><th>Control</th><th>Every</th><th>Last outcome</th>
              <th class="num">Population</th><th class="num">Exceptions</th><th>Next due</th></tr></thead>
              <tbody>${schedule}</tbody></table></div>`
          : empty("No control has been approved into the schedule yet."),
        `<a class="button ghost" href="#controls">All controls</a>`
      )}

      ${
        blocked.length
          ? card(
              "Proposed, and not testable",
              "The most useful thing on this page",
              `<div class="card-body">${blocked
                .map(
                  (c) => `<div class="callout warn">
                    <div class="callout-title">${esc(c.code)} · ${esc(c.title)}</div>
                    ${esc(c.blocked_reason)}
                  </div>`
                )
                .join("")}</div>`
            )
          : ""
      }`;
  }

  // ------------------------------------------------------------- discovery

  async function viewDiscovery() {
    const info = state.info;
    let profile = null;
    try {
      profile = await api(tenantUrl("/profile"));
    } catch (error) {
      profile = null;
    }

    header(
      "Discovery",
      "What this company is",
      "Countersign is pointed at the systems and works out the company from them. Everything below is a proposal with the evidence it was drawn from.",
      info.user && !profile
        ? `<button class="button emphasized" data-action="seed">Discover this company</button>`
        : ""
    );

    const sources = info.sources
      .map(
        (s) => `<div class="source-tile" title="${esc(s.note || s.mode)}">
          <span class="source-dot${s.connected ? "" : " off"}"></span>
          <div>
            <div class="name">${esc(s.source)}</div>
            <div class="meta">${esc(s.mode)} · ${
          s.datasets.length
            ? esc(s.datasets.join(", "))
            : s.mode === "disconnected"
            ? "not connected, so not testable"
            : "no datasets"
        }</div>
          </div>
        </div>`
      )
      .join("");

    if (!profile) {
      $("#content").innerHTML =
        card("Connected sources", `${info.sources.length} source systems`, `<div class="card-body grid three">${sources}</div>`) +
        card("Enterprise profile", "", empty("This company has not been discovered yet."));
      return;
    }

    const list = (values) =>
      values && values.length
        ? `<ul class="list-plain">${values.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
        : '<span class="tiny">none recorded</span>';

    $("#content").innerHTML = `
      ${card(
        "Connected sources",
        `${info.sources.filter((s) => s.connected).length} of ${info.sources.length} connected · live adapters activate when credentials are present`,
        `<div class="card-body grid three">${sources}</div>`
      )}

      ${card(
        "Enterprise profile",
        `Inferred sector: ${profile.sector.replace("_", " ")} · confidence ${Math.round(
          profile.confidence * 100
        )}%`,
        `<div class="card-body">
          <div class="callout">
            <div class="callout-title">Why this sector</div>
            ${esc(profile.sector_rationale)}
          </div>
          <div style="height:16px"></div>
          <dl class="kv">
            <dt>Legal entities</dt><dd>${list(profile.legal_entities)}</dd>
            <dt>Jurisdictions</dt><dd><div class="chips">${(profile.jurisdictions || [])
              .map((j) => `<span class="chip">${esc(j)}</span>`)
              .join("")}</div></dd>
            <dt>Headcount</dt><dd>${esc(profile.headcount_band || "–")}</dd>
            <dt>Critical systems</dt><dd>${list(profile.critical_systems)}</dd>
            <dt>Key processes</dt><dd>${list(profile.key_processes)}</dd>
            <dt>Regulatory perimeter</dt><dd>${list(profile.regulatory_perimeter)}</dd>
            <dt>Outsourced to</dt><dd>${list(profile.outsourcing_dependencies)}</dd>
            <dt>Drawn from</dt><dd>${evidenceChips(profile.evidence)}</dd>
          </dl>
        </div>`
      )}`;
  }

  // ---------------------------------------------------------- risk domains

  async function viewDomains() {
    const payload = await api(tenantUrl("/domains"));
    const domains = payload.domains || [];
    const canAct = Boolean(state.info.user);

    header(
      "Risk domains",
      "Proposed, and awaiting a person",
      "A domain is inert until somebody accepts it. Rejecting one is a decision the audit chain keeps."
    );

    if (!domains.length) {
      $("#content").innerHTML = card("Domains", "", empty("Nothing proposed yet. Run discovery first."));
      return;
    }

    const rows = domains
      .map(
        (d) => `<tr>
          <td><span class="code">${esc(d.code)}</span></td>
          <td>
            <span class="primary">${esc(d.title)}</span>
            <span class="secondary">${esc(d.description)}</span>
          </td>
          <td style="max-width:400px">
            <span class="small">${esc(d.why_this_company)}</span>
            <div class="chips" style="margin-top:8px">${(d.regulatory_drivers || [])
              .map((r) => `<span class="chip">${esc(r)}</span>`)
              .join("")}</div>
          </td>
          <td class="num">
            <strong>${d.inherent_score}</strong>
            <span class="secondary">L${d.inherent_likelihood} × I${d.inherent_impact}</span>
          </td>
          <td>${
            d.status === "accepted"
              ? statusPill("pos", "Accepted")
              : d.status === "rejected"
              ? statusPill("neutral", "Rejected")
              : statusPill("info", "Proposed")
          }
          ${d.decided_by ? `<span class="secondary">by ${esc(d.decided_by)}</span>` : ""}</td>
          <td>${
            d.status === "proposed" && canAct
              ? `<div class="header-actions">
                  <button class="button emphasized" data-action="domain-accept" data-code="${esc(d.code)}">Accept</button>
                  <button class="button danger" data-action="domain-reject" data-code="${esc(d.code)}">Reject</button>
                 </div>`
              : ""
          }</td>
        </tr>`
      )
      .join("");

    $("#content").innerHTML =
      card(
        "Proposed taxonomy",
        `${plural(domains.length, "domain")} · ordered by inherent exposure`,
        `<div class="card-body tight table-scroll"><table class="table">
          <thead><tr><th>Code</th><th style="width:230px">Domain</th><th>Why this company</th>
          <th class="num">Inherent</th><th style="width:150px">Status</th><th style="width:180px"></th></tr></thead>
          <tbody>${rows}</tbody></table></div>`
      ) +
      (payload.excluded && payload.excluded.length
        ? card(
            "Deliberately excluded",
            "A taxonomy that excludes nothing has not been thought about",
            `<div class="card-body">
              ${payload.coverage_note ? `<p class="muted" style="margin-bottom:12px">${esc(payload.coverage_note)}</p>` : ""}
              ${payload.excluded
                .map((reason) => `<div class="callout">${esc(reason)}</div>`)
                .join("")}
            </div>`
          )
        : "");
  }

  // -------------------------------------------------------------- controls

  async function viewControls() {
    const controls = await api(tenantUrl("/controls"));

    header(
      "Controls",
      `${plural(controls.length, "control")} in the programme`,
      "Every control binds to a deterministic test. A control naming a test that does not exist cannot be scheduled."
    );

    const rows = controls
      .map((c) => {
        const run = c.last_run;
        return `<tr class="clickable" data-href="#control/${encodeURIComponent(c.code)}">
          <td><span class="code">${esc(c.code)}</span></td>
          <td><span class="primary">${esc(c.title)}</span>
              <span class="secondary">${esc(c.domain_title)} · ${esc(c.nature)}</span></td>
          <td><span class="mono-chip">${esc(c.test_kind)}</span></td>
          <td><span class="chip">${esc(c.periodicity)}</span></td>
          <td>${
            c.status === "scheduled"
              ? statusPill("pos", "Scheduled")
              : c.runnable
              ? statusPill("info", "Awaiting approval")
              : statusPill("crit", "Not testable")
          }</td>
          <td>${run ? outcomePill(run.outcome) : '<span class="tiny">–</span>'}</td>
          <td class="num">${run ? esc(run.exception_count) : "–"}</td>
        </tr>`;
      })
      .join("");

    $("#content").innerHTML = card(
      "Control programme",
      "Click a control to see what it tested and what it found",
      `<div class="card-body tight table-scroll"><table class="table">
        <thead><tr><th>Code</th><th>Control</th><th>Test</th><th>Every</th>
        <th style="width:160px">Status</th><th style="width:150px">Last outcome</th>
        <th class="num">Exceptions</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`
    );
  }

  // --------------------------------------------------------- control detail

  async function viewControl(param = state.param) {
    if (!param) return;
    const control = await api(tenantUrl(`/controls/${encodeURIComponent(param)}`));
    const latest = control.runs[0] || null;
    const run = latest ? await api(tenantUrl(`/runs/${latest.id}`)) : null;
    const canAct = Boolean(state.info.user);

    header(
      control.code,
      control.title,
      control.objective,
      `${
        control.status === "proposed" && control.runnable && canAct
          ? `<button class="button emphasized" data-action="approve" data-code="${esc(
              control.code
            )}">Approve into the schedule</button>`
          : ""
      }
      ${
        control.status === "scheduled" && canAct
          ? `<button class="button" data-action="run" data-code="${esc(control.code)}">Run now</button>`
          : ""
      }
      <a class="button ghost" href="#controls">Back</a>`
    );

    const facts = run
      ? `<div class="object-facts">
          <div class="object-fact"><div class="label">Outcome</div><div class="value">${outcomePill(
            run.outcome
          )}</div></div>
          <div class="object-fact"><div class="label">Population</div><div class="value">${run.population_size}</div></div>
          <div class="object-fact"><div class="label">Exceptions</div><div class="value" style="color:${
            run.exception_count ? "var(--neg)" : "inherit"
          }">${run.exception_count}</div></div>
          <div class="object-fact"><div class="label">Suppressed</div><div class="value">${run.suppressed_count}</div></div>
          <div class="object-fact"><div class="label">Tolerance</div><div class="value">${control.tolerance}</div></div>
          <div class="object-fact"><div class="label">Period tested</div><div class="value" style="font-size:14px">${esc(
            run.period_start
          )} → ${esc(run.period_end)}</div></div>
        </div>`
      : "";

    if (!control.runnable) {
      $("#content").innerHTML =
        card(
          "This control cannot run yet",
          "",
          `<div class="card-body"><div class="callout warn">
            <div class="callout-title">No evidence source</div>${esc(control.blocked_reason)}
          </div>
          <p class="muted small" style="margin-top:12px">${esc(control.automation_note || "")}</p>
          </div>`
        ) + definitionCard(control);
      return;
    }

    if (!run) {
      $("#content").innerHTML =
        card("Not run yet", "", empty("Approve the control and run it to see a report here.")) +
        definitionCard(control);
      return;
    }

    const tabs = ["Report", "Population", "Findings", "Challenge", "Definition", "Trace"];
    const active = state.tab && tabs.includes(state.tab) ? state.tab : "Report";

    $("#content").innerHTML = `
      <section class="card">
        <div class="card-head"><div>
          <h2>Run ${run.id} · ${esc(stamp(run.finished_at))}</h2>
          <p class="sub">${esc(
            run.model_mode === "demo"
              ? "Report composed deterministically. The outcome was counted from the population either way."
              : `Report written by the Strands agent graph (${run.model_mode}).`
          )}</p>
        </div></div>
        <div class="card-body" style="padding-bottom:6px">${facts}</div>
        <div class="tabs">${tabs
          .map(
            (t) =>
              `<button class="tab${t === active ? " is-active" : ""}" data-tab="${t}">${t}</button>`
          )
          .join("")}</div>
        <div class="card-body">${controlTab(active, control, run)}</div>
      </section>`;
  }

  function controlTab(tab, control, run) {
    if (tab === "Report") return reportTab(run);
    if (tab === "Population") return populationTab(run);
    if (tab === "Findings") return findingsTab(run);
    if (tab === "Challenge") return challengeTab(run);
    if (tab === "Definition") return definitionBody(control);
    return traceTab(run);
  }

  function reportTab(run) {
    const injections = run.injection_signals || [];
    return `<div class="report">
      <p class="summary">${esc(run.summary)}</p>
      ${
        injections.length
          ? `<div style="height:14px"></div>
             <div class="callout danger">
               <div class="callout-title">An instruction was found inside the evidence, and not followed</div>
               ${esc(
                 `${plural(
                   injections.length,
                   "detector"
                 )} fired on ${[...new Set(injections.map((i) => i.locator))].join(", ")}. ` +
                   "The outcome above was counted from the population before any model read this " +
                   "document, so the instruction had nothing to act on."
               )}
               <div class="chips" style="margin-top:9px">${[
                 ...new Set(injections.map((i) => i.detector)),
               ]
                 .map((d) => `<span class="chip">${esc(d)}</span>`)
                 .join("")}</div>
               ${[...new Set(injections.map((i) => i.excerpt))]
                 .slice(0, 2)
                 .map((excerpt) => `<div class="quote">${esc(excerpt)}</div>`)
                 .join("")}
             </div>`
          : ""
      }
      ${
        run.observations && run.observations.length
          ? `<div style="height:14px"></div>
             <p class="eyebrow">Observations</p>
             <ul class="list-plain" style="margin-top:6px">${run.observations
               .map((o) => `<li>${esc(o)}</li>`)
               .join("")}</ul>`
          : ""
      }
      ${
        run.not_tested && run.not_tested.length
          ? `<div style="height:14px"></div>
             <div class="callout warn"><div class="callout-title">Not tested, so not concluded</div>
             ${esc(
               `${plural(
                 run.not_tested.length,
                 "member"
               )} of the population could not be tested. The run is reported as ` +
                 "inconclusive rather than effective, and it cannot close an open finding."
             )}
             <div style="height:9px"></div>
             ${run.not_tested.map((n) => esc(n)).join("<br>")}</div>`
          : ""
      }
    </div>`;
  }

  function populationTab(run) {
    if (!run.population.length) return empty("The population was empty.");
    const rows = run.population
      .map(
        (item) => `<tr>
        <td>${
          item.disposition === "suppressed"
            ? statusPill("info", "Suppressed")
            : item.passed
            ? statusPill("pos", "Pass")
            : statusPill("neg", "Exception")
        }</td>
        <td><span class="primary">${esc(item.label)}</span>
            <span class="secondary code">${esc(item.subject)}</span></td>
        <td>${esc(item.reason)}</td>
        <td>${evidenceChips(item.evidence)}</td>
      </tr>`
      )
      .join("");
    return `<p class="tiny" style="margin-bottom:10px">Every member of the population is listed, not a sample. Exceptions and suppressed items sort first.</p>
      <div class="table-scroll"><table class="table">
      <thead><tr><th style="width:120px">Result</th><th style="width:280px">Subject</th><th>Reason</th><th style="width:220px">Evidence</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  function findingsTab(run) {
    if (!run.findings.length)
      return empty("No finding was raised. The control operated as designed.");
    return run.findings.map(findingBlock).join("");
  }

  function findingBlock(finding) {
    return `<div class="finding-block">
      <div class="finding-head">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          ${severityPill(finding.severity)}
          <strong>${esc(finding.title)}</strong>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          ${statusPill(FINDING_STATUS[finding.status], FINDING_LABEL[finding.status])}
          <a class="button ghost" href="#finding/${finding.id}">Open</a>
        </div>
      </div>
      <div class="finding-grid">
        <div class="finding-part"><div class="label">Condition</div><p>${esc(
          finding.condition_text
        )}</p></div>
        <div class="finding-part"><div class="label">Criterion</div><p>${esc(
          finding.criterion_text
        )}</p></div>
        <div class="finding-part"><div class="label">Cause</div><p>${esc(
          finding.cause_text
        )}</p></div>
        <div class="finding-part"><div class="label">Effect</div><p>${esc(
          finding.effect_text
        )}</p></div>
      </div>
      <div class="finding-part" style="border-bottom:0">
        <div class="label">Proposed remediation${
          finding.proposed_owner ? ` · ${esc(finding.proposed_owner)}` : ""
        }</div>
        <p>${esc(finding.proposed_remediation)}</p>
        <div style="height:10px"></div>
        ${evidenceChips(finding.evidence)}
      </div>
    </div>`;
  }

  function challengeTab(run) {
    const challenges = run.challenges || [];
    if (!challenges.length)
      return empty("Nothing was raised, so there was nothing to argue against.");
    return `<p class="tiny" style="margin-bottom:12px">Each finding is argued against before a person is asked to sign it. A finding that cannot survive the strongest case against it should not reach a committee.</p>
      ${challenges
        .map(
          (c) => `<div class="finding-block">
        <div class="finding-head">
          <strong>${esc(c.finding_title)}</strong>
          ${c.survives ? statusPill("neg", "Survives challenge") : statusPill("neutral", "Withdrawn")}
        </div>
        <div class="finding-grid">
          <div class="finding-part"><div class="label">Strongest counterargument</div><p>${esc(
            c.strongest_counterargument
          )}</p></div>
          <div class="finding-part"><div class="label">Why it ${
            c.survives ? "still stands" : "does not stand"
          }</div><p>${esc(c.reason)}</p></div>
        </div>
      </div>`
        )
        .join("")}`;
  }

  function traceTab(run) {
    const trace = run.trace || [];
    return `<p class="tiny" style="margin-bottom:10px">The agent lifecycle recorded for this run, and the digest of the population it walked.</p>
      <dl class="kv">
        <dt>Result digest</dt><dd><span class="mono-chip">${esc(run.result_digest)}</span></dd>
        <dt>Model mode</dt><dd>${esc(run.model_mode)}</dd>
        <dt>Agent lifecycle</dt><dd>${
          trace.length
            ? `<ul class="list-plain">${trace
                .map((t) => `<li><span class="code">${esc(t.agent)}</span> · ${esc(t.event)}</li>`)
                .join("")}</ul>`
            : '<span class="tiny">none recorded</span>'
        }</dd>
      </dl>`;
  }

  function definitionBody(control) {
    return `<dl class="kv">
      <dt>Objective</dt><dd>${esc(control.objective)}</dd>
      <dt>Bound test</dt><dd><span class="mono-chip">${esc(control.test_kind)}</span></dd>
      <dt>Parameters</dt><dd><span class="code">${esc(
        JSON.stringify(control.parameters)
      )}</span></dd>
      <dt>Frequency</dt><dd>${esc(control.periodicity)}</dd>
      <dt>Why that frequency</dt><dd>${esc(control.periodicity_rationale)}</dd>
      <dt>Tolerance</dt><dd>${control.tolerance} exception(s) before the control fails</dd>
      <dt>Severity if failed</dt><dd>${severityPill(control.severity_if_failed)}</dd>
      <dt>Owner</dt><dd>${esc(control.owner_role || "–")}</dd>
      ${
        control.automation_note
          ? `<dt>Automation note</dt><dd>${esc(control.automation_note)}</dd>`
          : ""
      }
      ${
        control.residual_gap
          ? `<dt>What it does not cover</dt><dd>${esc(control.residual_gap)}</dd>`
          : ""
      }
      <dt>Approved by</dt><dd>${
        control.approved_by
          ? `${esc(control.approved_by)} on ${esc(stamp(control.approved_at))}`
          : '<span class="tiny">not approved</span>'
      }</dd>
    </dl>`;
  }

  const definitionCard = (control) =>
    card("Definition", "", `<div class="card-body">${definitionBody(control)}</div>`);

  // -------------------------------------------------------------- findings

  async function viewFindings() {
    const findings = await api(tenantUrl("/findings"));
    header(
      "Findings",
      `${plural(findings.length, "finding")} raised`,
      "A finding is closed by evidence, not by agreement. Re-run the control; a clean population closes it."
    );

    if (!findings.length) {
      $("#content").innerHTML = card("Findings", "", empty("Nothing has been raised."));
      return;
    }

    const rows = findings
      .map(
        (f) => `<tr class="clickable" data-href="#finding/${f.id}">
        <td>${severityPill(f.severity)}</td>
        <td><span class="primary">${esc(f.title)}</span>
            <span class="secondary">${esc(f.condition_text).slice(0, 150)}…</span></td>
        <td><span class="code">${esc(f.control_code)}</span></td>
        <td>${esc(f.proposed_owner || "–")}</td>
        <td>${statusPill(FINDING_STATUS[f.status], FINDING_LABEL[f.status])}</td>
        <td>${esc(day(f.created_at))}</td>
      </tr>`
      )
      .join("");

    $("#content").innerHTML = card(
      "Worklist",
      "Ordered by severity",
      `<div class="card-body tight table-scroll"><table class="table">
        <thead><tr><th style="width:110px">Severity</th><th>Finding</th><th style="width:130px">Control</th>
        <th style="width:200px">Owner</th><th style="width:170px">Status</th><th style="width:110px">Raised</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`
    );
  }

  async function viewFinding(param = state.param) {
    if (!param) return;
    const finding = await api(tenantUrl(`/findings/${encodeURIComponent(param)}`));
    const canAct = Boolean(state.info.user);
    const challenge = finding.challenge && finding.challenge.finding_title ? finding.challenge : null;

    header(
      "Finding",
      finding.title,
      `Raised by ${finding.control_code} on ${day(finding.created_at)}`,
      `${
        canAct && finding.status !== "closed"
          ? `<button class="button" data-action="finding-agree" data-id="${finding.id}">Agree remediation</button>
             <button class="button danger" data-action="finding-accept" data-id="${finding.id}">Accept the risk</button>`
          : ""
      }
      <a class="button ghost" href="#findings">Back</a>`
    );

    $("#content").innerHTML = `
      ${card(
        `${finding.severity.toUpperCase()} · ${FINDING_LABEL[finding.status]}`,
        finding.proposed_owner ? `Proposed owner: ${finding.proposed_owner}` : "",
        `<div class="card-body">${findingBlock(finding)}</div>`
      )}

      ${
        challenge
          ? card(
              "Challenged before it reached you",
              challenge.survives ? "It survived" : "It was withdrawn",
              `<div class="card-body">
                <p class="eyebrow">Strongest counterargument</p>
                <p style="margin-top:5px">${esc(challenge.strongest_counterargument)}</p>
                <div style="height:12px"></div>
                <p class="eyebrow">Why it ${challenge.survives ? "still stands" : "does not stand"}</p>
                <p style="margin-top:5px">${esc(challenge.reason)}</p>
              </div>`
            )
          : ""
      }

      ${card(
        "Population this rests on",
        `${plural((finding.subjects || []).length, "item")}`,
        `<div class="card-body"><div class="chips">${(finding.subjects || [])
          .map((s) => `<span class="chip evidence">${esc(s)}</span>`)
          .join("")}</div></div>`
      )}

      ${
        finding.decided_by || finding.decision_note
          ? card(
              "Decision",
              finding.decided_by ? `${finding.decided_by} · ${stamp(finding.decided_at)}` : "",
              `<div class="card-body"><p>${esc(finding.decision_note || "–")}</p></div>`
            )
          : ""
      }

      ${card(
        "How this closes",
        "",
        `<div class="card-body"><div class="callout">
          A person cannot close this. It closes when ${esc(
            finding.control_code
          )} runs again over a fresh population and comes back with no exceptions, and the closing run is recorded against it.
        </div></div>`
      )}`;
  }

  // -------------------------------------------------------------- register

  async function viewRegister() {
    const obligations = await api(tenantUrl("/obligations"));
    header(
      "Obligation register",
      `${plural(obligations.length, "obligation")}`,
      "Countersign hardcodes no threshold. Every limit a control tests against is a row here, maintained by the company, and citable in a report."
    );

    const rows = obligations
      .map(
        (o) => `<tr>
        <td><span class="code">${esc(o.reference)}</span></td>
        <td><span class="primary">${esc(o.regime)}</span></td>
        <td>${esc(o.requirement)}</td>
        <td class="num">${
          o.quantitative_limit != null
            ? `<strong>${esc(o.quantitative_limit)}</strong> <span class="secondary">${esc(
                o.limit_unit || ""
              )}</span>`
            : "–"
        }</td>
        <td class="tiny">${esc(o.source_document || "–")}</td>
      </tr>`
      )
      .join("");

    $("#content").innerHTML = card(
      "Register",
      "The thresholds every control measures against",
      `<div class="card-body tight table-scroll"><table class="table">
        <thead><tr><th style="width:130px">Reference</th><th style="width:220px">Regime</th>
        <th>Requirement</th><th class="num" style="width:110px">Limit</th><th style="width:220px">Source</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`
    );
  }

  // -------------------------------------------------------------- activity

  async function viewActivity() {
    const audit = await api(tenantUrl("/audit"));
    header(
      "Activity",
      audit.detail,
      "Append-only. Each entry's hash covers the one before it, so removing or editing history breaks the chain at the point it was touched."
    );

    const rows = audit.events
      .map(
        (e) => `<li>
        <span class="when">${esc(stamp(e.at))}</span>
        <span class="who${e.actor.startsWith("agent:") ? " is-agent" : ""}">${esc(e.actor)}</span>
        <span class="what">${esc(e.event)} <span class="tiny">${esc(e.entity)}</span></span>
        <span class="hash">${esc(e.hash.slice(0, 12))}</span>
      </li>`
      )
      .join("");

    $("#content").innerHTML = card(
      audit.intact ? "Chain intact" : "Chain broken",
      `${audit.events.length} most recent events · agents in amber, people in grey`,
      `<div class="card-body tight"><ul class="timeline">${rows}</ul></div>`
    );
  }

  // ---------------------------------------------------------------- header

  function header(eyebrow, title, lede, actions) {
    $("#page-header").innerHTML = `<div class="header-row">
      <div>
        <p class="eyebrow">${esc(eyebrow)}</p>
        <h1>${esc(title)}</h1>
        ${lede ? `<p class="lede">${esc(lede)}</p>` : ""}
      </div>
      ${actions ? `<div class="header-actions">${actions}</div>` : ""}
    </div>`;
  }

  // ---------------------------------------------------------------- dialog

  function ask({ kicker, title, copy, fields, submit }) {
    return new Promise((resolve) => {
      const dialog = $("#dialog");
      $("#dialog-kicker").textContent = kicker || "";
      $("#dialog-title").textContent = title;
      $("#dialog-copy").textContent = copy || "";
      $("#dialog-error").textContent = "";
      $("#dialog-submit").textContent = submit || "Confirm";
      $("#dialog-fields").innerHTML = (fields || [])
        .map(
          (field) =>
            `<label>${esc(field.label)}${
              field.type === "textarea"
                ? `<textarea name="${esc(field.name)}" ${
                    field.required ? "required" : ""
                  } placeholder="${esc(field.placeholder || "")}"></textarea>`
                : `<input name="${esc(field.name)}" type="${field.type || "text"}" ${
                    field.required ? "required" : ""
                  } placeholder="${esc(field.placeholder || "")}" value="${esc(
                    field.value || ""
                  )}">`
            }</label>`
        )
        .join("");

      const form = $("#dialog-form");
      const finish = (value) => {
        form.onsubmit = null;
        dialog.close();
        resolve(value);
      };
      form.onsubmit = (event) => {
        event.preventDefault();
        const data = Object.fromEntries(new FormData(form).entries());
        finish(data);
      };
      $("#dialog-cancel").onclick = () => finish(null);
      $("#dialog-close").onclick = () => finish(null);
      dialog.showModal();
      const first = form.querySelector("input, textarea");
      if (first) first.focus();
    });
  }

  // --------------------------------------------------------------- actions

  async function guard(work, message) {
    try {
      await work();
      if (message) toast(message, "good");
      await refresh();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  async function handleAction(action, element) {
    if (action === "signin") return signIn();

    if (action === "seed")
      return guard(async () => {
        toast("Discovering the company, designing the programme and running it…");
        await api(tenantUrl("/seed"), { method: "POST" });
      }, "Programme rebuilt and every scheduled control has run.");

    if (action === "run-due")
      return guard(async () => {
        const runs = await api(tenantUrl("/run-due"), { method: "POST" });
        toast(
          runs.length ? `${plural(runs.length, "control")} ran.` : "Nothing was due.",
          "good"
        );
      });

    if (action === "run")
      return guard(async () => {
        const outcome = await api(
          tenantUrl(`/controls/${encodeURIComponent(element.dataset.code)}/run`),
          { method: "POST", body: { lookback: 91 } }
        );
        toast(
          `${outcome.control}: ${OUTCOME_LABEL[outcome.outcome]}, ${outcome.exceptions} of ${
            outcome.population
          } failed.`,
          outcome.outcome === "effective" ? "good" : "bad"
        );
      });

    if (action === "approve")
      return guard(
        () =>
          api(tenantUrl(`/controls/${encodeURIComponent(element.dataset.code)}/approve`), {
            method: "POST",
          }),
        "Control approved into the schedule."
      );

    if (action === "domain-accept" || action === "domain-reject")
      return guard(
        () =>
          api(tenantUrl(`/domains/${encodeURIComponent(element.dataset.code)}/decision`), {
            method: "POST",
            body: { accept: action === "domain-accept" },
          }),
        action === "domain-accept" ? "Domain accepted." : "Domain rejected."
      );

    if (action === "finding-agree") {
      const answer = await ask({
        kicker: "Remediation",
        title: "Agree the remediation",
        copy: "Record what will be done and by whom. The finding stays open until the control comes back clean.",
        fields: [
          {
            name: "note",
            label: "What has been agreed",
            type: "textarea",
            required: true,
            placeholder: "Owner, action and the date it will be done by.",
          },
        ],
        submit: "Record",
      });
      if (!answer) return;
      return guard(
        () =>
          api(tenantUrl(`/findings/${element.dataset.id}/decision`), {
            method: "POST",
            body: { status: "remediation_agreed", note: answer.note },
          }),
        "Remediation recorded."
      );
    }

    if (action === "finding-accept") {
      const answer = await ask({
        kicker: "Risk acceptance",
        title: "Accept this risk",
        copy: "Somebody will be asked about this decision a year from now. The reason is recorded against your name in the audit chain.",
        fields: [
          {
            name: "note",
            label: "Why the risk is being accepted",
            type: "textarea",
            required: true,
            placeholder: "At least a sentence. Who agreed, on what basis, and until when.",
          },
        ],
        submit: "Accept the risk",
      });
      if (!answer) return;
      return guard(
        () =>
          api(tenantUrl(`/findings/${element.dataset.id}/decision`), {
            method: "POST",
            body: { status: "risk_accepted", note: answer.note },
          }),
        "Risk acceptance recorded."
      );
    }
  }

  async function signIn() {
    if (state.info && state.info.user) {
      await api("/api/session", { method: "DELETE" });
      toast("Signed out.");
      return refresh();
    }
    const answer = await ask({
      kicker: "Console",
      title: "Sign in",
      copy: "Accepting a domain, approving a control and dispositioning a finding all need a named person. Demonstration credentials: risk / countersign",
      fields: [
        { name: "username", label: "User", required: true, value: "risk" },
        { name: "password", label: "Password", type: "password", required: true, value: "countersign" },
      ],
      submit: "Sign in",
    });
    if (!answer) return;
    try {
      await api("/api/session", { method: "POST", body: answer });
      toast("Signed in.", "good");
      await refresh();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  // ---------------------------------------------------------------- router

  const VIEWS = {
    overview: viewOverview,
    discovery: viewDiscovery,
    domains: viewDomains,
    controls: viewControls,
    control: viewControl,
    findings: viewFindings,
    finding: viewFinding,
    register: viewRegister,
    activity: viewActivity,
  };

  function readHash() {
    const target = hashTarget();
    state.view = target.view;
    state.param = target.param;
  }

  function hashTarget() {
    const raw = (location.hash || "#overview").slice(1);
    const [view, param] = raw.split("/");
    return {
      view: VIEWS[view] ? view : "overview",
      param: param ? decodeURIComponent(param) : null,
    };
  }

  async function refresh() {
    // Captured, not read back from state: a newer navigation mutates state
    // while this one is still fetching, and comparing against the mutated
    // value would say everything agrees when it does not.
    const startedView = state.view;
    const startedParam = state.param;
    const mark = { view: startedView, param: startedParam, at: Date.now(), painted: false };
    state.renders.push(mark);
    if (state.renders.length > 40) state.renders.shift();

    state.info = await api(
      state.tenant ? `/api/tenants/${encodeURIComponent(state.tenant)}/state` : "/api/state"
    );
    state.tenant = state.info.tenant;
    renderShell();
    try {
      // The param is captured for the same reason the view is: a view fetched
      // by id must fetch the id this render was for, not the id the address bar
      // has moved on to while the fetch was in flight.
      await VIEWS[startedView](startedParam);
      mark.painted = true;
    } catch (error) {
      mark.error = String(error);
      $("#content").innerHTML = card("Something went wrong", "", empty(error.message));
    }
    // Which view the content area is actually showing. Two views can hold the
    // same kind of table, so "a table appeared" is not evidence that the view
    // you asked for is the view you are looking at.
    $("#content").dataset.view = startedView;
    $("#content").dataset.param = startedParam || "";

    // A view is fetched asynchronously, so a slow one can finish after the
    // person has already navigated somewhere else and paint over the top of
    // where they actually are. If the address bar disagrees with what this
    // render just painted, the address bar is right.
    const wanted = hashTarget();
    if (wanted.view !== startedView || wanted.param !== startedParam) return route();
  }

  async function route() {
    readHash();
    state.tab = null;
    await refresh();
  }

  // ----------------------------------------------------------------- wiring

  document.addEventListener("click", async (event) => {
    const action = event.target.closest("[data-action]");
    if (action) {
      event.preventDefault();
      const original = action.textContent;
      action.disabled = true;
      action.textContent = "Working…";
      try {
        await handleAction(action.dataset.action, action);
      } finally {
        action.disabled = false;
        action.textContent = original;
      }
      return;
    }

    const tab = event.target.closest(".tab");
    if (tab) {
      state.tab = tab.dataset.tab;
      await VIEWS[state.view]();
      return;
    }

    const nav = event.target.closest(".nav-item");
    if (nav) {
      location.hash = `#${nav.dataset.view}`;
      return;
    }

    const row = event.target.closest("tr.clickable");
    if (row && !event.target.closest("a, button")) {
      location.hash = row.dataset.href;
    }
  });

  $("#auth-button").addEventListener("click", signIn);

  $("#tenant-select").addEventListener("change", async (event) => {
    state.tenant = event.target.value;
    location.hash = "#overview";
    await route();
  });

  window.addEventListener("hashchange", route);

  route().catch((error) => {
    document.body.innerHTML = `<div class="empty">Countersign could not start: ${esc(
      error.message
    )}</div>`;
  });

  window.Countersign = { state, refresh, api };
})();

/* Guided demonstration.
   Inert unless the URL carries ?demo=1.

   It drives the real console: the real router, the real controls, and every
   number it points at came back from the real API. That is the only reason it
   is worth recording.

   window.__COUNTERSIGN_TIMING, an array of per-beat seconds, paces it to a
   voice-over. Without one it falls back to a reading-speed estimate.
   Escape stops it and hands the product back. */

(() => {
  "use strict";

  const parameters = new URLSearchParams(location.search);
  if (parameters.get("demo") !== "1") return;

  const manual = window.__COUNTERSIGN_DEMO_MANUAL === true;
  const timing = window.__COUNTERSIGN_TIMING || null;

  const state = {
    ready: false,
    finished: false,
    failures: [],
    index: -1,
    stopped: false,
  };

  // ---------------------------------------------------------------- chrome

  const style = document.createElement("style");
  style.textContent = `
    .demo-caption {
      position: fixed; left: 50%; bottom: 34px; transform: translateX(-50%);
      max-width: min(1080px, calc(100vw - 80px)); z-index: 9000;
      background: rgba(19, 27, 35, .95); color: #fff;
      font: 500 19px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      padding: 15px 26px; border-radius: 12px; text-align: center;
      box-shadow: 0 12px 40px rgba(0,0,0,.34); opacity: 0; transition: opacity .28s ease;
      pointer-events: none;
    }
    .demo-caption.on { opacity: 1; }
    .demo-ring {
      position: fixed; z-index: 8900; border: 2.5px solid #0064d9; border-radius: 10px;
      box-shadow: 0 0 0 4000px rgba(19,27,35,.42), 0 0 22px rgba(0,100,217,.55);
      pointer-events: none; opacity: 0; transition: all .38s cubic-bezier(.4,0,.2,1);
    }
    .demo-ring.on { opacity: 1; }
    .demo-cursor {
      position: fixed; z-index: 9100; width: 20px; height: 20px; margin: -10px 0 0 -10px;
      border-radius: 50%; background: rgba(0,100,217,.28); border: 2px solid #0064d9;
      pointer-events: none; opacity: 0; transition: all .42s cubic-bezier(.4,0,.2,1);
    }
    .demo-cursor.on { opacity: 1; }
    .demo-cursor.tap { transform: scale(.6); background: rgba(0,100,217,.55); }
    .demo-card {
      position: fixed; inset: 0; z-index: 9200; display: grid; place-content: center;
      text-align: center; background: #131b23; color: #fff; padding: 40px;
      opacity: 0; transition: opacity .4s ease; pointer-events: none;
    }
    .demo-card.on { opacity: 1; }
    .demo-card h1 {
      font: 600 58px/1.1 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      letter-spacing: -.025em; margin: 0 0 18px;
    }
    .demo-card p {
      font: 400 24px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      color: #b9c6d4; max-width: 900px; margin: 0 auto;
    }
    .demo-card .mark { width: 62px; height: 62px; margin: 0 auto 26px; display: block; }
  `;
  document.head.append(style);

  const caption = document.createElement("div");
  caption.className = "demo-caption";
  const ring = document.createElement("div");
  ring.className = "demo-ring";
  const cursor = document.createElement("div");
  cursor.className = "demo-cursor";
  const card = document.createElement("div");
  card.className = "demo-card";
  card.innerHTML = `<img class="mark" src="/assets/mark.svg" alt=""><h1></h1><p></p>`;
  document.body.append(ring, cursor, caption, card);

  // ------------------------------------------------------------- utilities

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function fail(message) {
    state.failures.push(`beat ${state.index + 1}: ${message}`);
  }

  async function waitFor(selector, timeout = 12000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const node = document.querySelector(selector);
      if (node) return node;
      await sleep(80);
    }
    fail(`never found ${selector}`);
    return null;
  }

  async function goto(view, param) {
    const fragment = param ? `#${view}/${encodeURIComponent(param)}` : `#${view}`;
    if (location.hash !== fragment) location.hash = fragment;
    return waitFor(`#content[data-view="${view}"][data-param="${param || ""}"]`);
  }

  function say(text) {
    caption.textContent = text;
    caption.classList.add("on");
  }

  const hush = () => caption.classList.remove("on");
  const unspotlight = () => ring.classList.remove("on");

  async function spotlight(selector, pad = 10) {
    const node = typeof selector === "string" ? await waitFor(selector) : selector;
    if (!node) return null;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(420);
    const box = node.getBoundingClientRect();
    ring.style.left = `${box.left - pad}px`;
    ring.style.top = `${box.top - pad}px`;
    ring.style.width = `${box.width + pad * 2}px`;
    ring.style.height = `${box.height + pad * 2}px`;
    ring.classList.add("on");
    return node;
  }

  async function point(node) {
    const box = node.getBoundingClientRect();
    cursor.style.left = `${box.left + box.width / 2}px`;
    cursor.style.top = `${box.top + box.height / 2}px`;
    cursor.classList.add("on");
    await sleep(420);
  }

  async function click(selector, label) {
    const node = typeof selector === "string" ? await waitFor(selector) : selector;
    if (!node) {
      fail(`could not click ${label || selector}`);
      return null;
    }
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(300);
    await point(node);
    cursor.classList.add("tap");
    await sleep(150);
    node.click();
    cursor.classList.remove("tap");
    await sleep(500);
    return node;
  }

  async function showCard(title, subtitle) {
    card.querySelector("h1").textContent = title;
    card.querySelector("p").textContent = subtitle || "";
    card.classList.add("on");
    unspotlight();
    hush();
  }

  async function hideCard() {
    card.classList.remove("on");
    await sleep(420);
  }

  const text = (selector) => (document.querySelector(selector) || {}).innerText || "";
  const tab = (name) => [...document.querySelectorAll(".tab")].find((t) => t.textContent === name);

  function mustContain(selector, needle, why) {
    // innerText applies CSS text-transform, so a label the stylesheet
    // uppercases comes back uppercased. Compare case-insensitively.
    if (!text(selector).toLowerCase().includes(needle.toLowerCase())) {
      fail(`${why}: "${needle}" is not on screen`);
    }
  }

  // ---------------------------------------------------------------- beats

  const beats = [
    {
      say: "Countersign. A second line of defence that runs itself.",
      run: async () => {
        await showCard("Countersign", "A second line of defence that runs itself");
        await sleep(600);
      },
    },
    {
      say:
        "In a regulated firm, the first line does the work and asserts it followed the rules. " +
        "The second line is the function that checks.",
      run: async () => {
        await showCard(
          "The second line",
          "The function that checks whether the controls a company promised are the controls it runs"
        );
      },
    },
    {
      say:
        "Almost none of that is judgement. It is walking populations. Every change merged to " +
        "production. Every account belonging to somebody who left. There is always more of it " +
        "than there are people, so it gets sampled.",
      run: async () => {
        await showCard(
          "Sampling is how a control programme becomes a document about work",
          "I audit for a living. Every constraint in this product is one I have had to satisfy."
        );
      },
    },
    {
      say: "This is Kestrel Pay, an electronic money institution. Countersign has been running its controls.",
      run: async () => {
        await hideCard();
        await goto("overview");
        await waitFor(".tile");
        mustContain("#content", "Open findings", "the overview did not render");
      },
    },
    {
      say:
        "Eight controls on their own schedules. Six findings waiting on a person. Two controls " +
        "came back clean and are asking for nothing.",
      run: async () => {
        await spotlight(".grid.kpi");
      },
    },
    {
      say: "Nobody told it what this company is. It was pointed at the systems.",
      run: async () => {
        unspotlight();
        await goto("discovery");
        await spotlight(".card:first-of-type .card-body");
      },
    },
    {
      say:
        "It read the obligation register and the payment ledger, and concluded a licensed " +
        "payments firm. The rationale states what it ruled out.",
      run: async () => {
        unspotlight();
        await spotlight(".callout");
        mustContain("#content", "financial services", "the sector was not inferred");
      },
    },
    {
      say:
        "From that, a risk taxonomy. Every domain cites what was found in this estate. A generic " +
        "risk statement fails validation.",
      run: async () => {
        unspotlight();
        await goto("domains");
        await spotlight("table.table tbody tr:first-child");
      },
    },
    {
      say:
        "And what it deliberately left out, with the reason. A taxonomy that excludes nothing has " +
        "not been thought about.",
      run: async () => {
        unspotlight();
        const excluded = document.querySelectorAll(".card")[1];
        if (!excluded) fail("the deliberately-excluded panel is missing");
        else await spotlight(excluded);
      },
    },
    {
      say:
        "For each domain, controls. Each binds to a deterministic test, and states how often it " +
        "runs and why that frequency.",
      run: async () => {
        unspotlight();
        await goto("controls");
        await waitFor("table.table tbody tr");
      },
    },
    {
      say:
        "One is marked not testable, because the source it needs is not connected. It stays on " +
        "the page, and approval is refused.",
      run: async () => {
        const blocked = [...document.querySelectorAll("tbody tr")].find((row) =>
          row.innerText.includes("Not testable")
        );
        if (!blocked) fail("no control is shown as not testable");
        else await spotlight(blocked);
      },
    },
    {
      say: "Open the incident notification control. Three of four major incidents breached the limit.",
      run: async () => {
        unspotlight();
        await goto("control", "DORA-INC-01");
        await spotlight(".object-facts");
        mustContain("#content", "Not effective", "the outcome did not render");
      },
    },
    {
      say:
        "Every internal system recorded them as on time. The obligation register says four hours. " +
        "The incident plan and the Jira automation both say twenty-four.",
      run: async () => {
        unspotlight();
        const observation = [...document.querySelectorAll("#content li")].find((item) =>
          item.innerText.includes("24 hours")
        );
        if (!observation) fail("the threshold divergence is not reported");
        else await spotlight(observation);
      },
    },
    {
      say:
        "That incident plan also carries a block addressed to automated reviewers, demanding the " +
        "outcome be reported as effective and the timings left out.",
      run: async () => {
        unspotlight();
        await spotlight(".callout.danger");
      },
    },
    {
      say:
        "It was reported and refused. The outcome was counted from four records before any model " +
        "read that document.",
      run: async () => {
        const quote = document.querySelector(".quote");
        if (!quote) fail("the injection excerpt is not shown");
        else await spotlight(quote);
      },
    },
    {
      say: "Every member of the population, with its reason and its evidence. A whole population, not a sample.",
      run: async () => {
        unspotlight();
        await click(tab("Population"), "the Population tab");
        await waitFor("table.table tbody tr");
      },
    },
    {
      say: "The finding, in the four parts a risk committee expects. Condition, criterion, cause, effect.",
      run: async () => {
        await click(tab("Findings"), "the Findings tab");
        await spotlight(".finding-grid");
      },
    },
    {
      say:
        "And the strongest argument against it, made before anybody is asked to sign. A finding " +
        "that fails that test should stop here.",
      run: async () => {
        unspotlight();
        await click(tab("Challenge"), "the Challenge tab");
        await spotlight(".finding-block");
      },
    },
    {
      say: "A person can accept the risk instead. Saying why is part of the transaction.",
      run: async () => {
        unspotlight();
        await goto("findings");
        await click("table.table tbody tr", "the first finding");
        await waitFor(".finding-grid");
        await click('[data-action="finding-accept"]', "accept the risk");
        const box = await waitFor("#dialog textarea");
        if (box) {
          box.value = "fine";
          box.dispatchEvent(new Event("input", { bubbles: true }));
        }
        await sleep(500);
        await click("#dialog-submit", "confirm");
      },
    },
    {
      say:
        "Refused. Somebody will be asked about that decision a year from now, and the reason is " +
        "recorded against their name.",
      run: async () => {
        const toast = await waitFor(".toast.is-bad", 8000);
        if (toast && !toast.textContent.includes("stated reason")) {
          fail(`the refusal did not explain itself: ${toast.textContent}`);
        }
        if (toast) await spotlight(toast);
      },
    },
    {
      say:
        "Closing works the same way. A finding closes when the control runs again over a fresh " +
        "population and comes back clean.",
      run: async () => {
        unspotlight();
        const closes = [...document.querySelectorAll(".callout")].find((node) =>
          node.innerText.includes("closes when")
        );
        if (!closes) fail("the closure rule is not stated on the finding");
        else await spotlight(closes);
      },
    },
    {
      say: "Every state change is appended to a hash chain. The agents proposed. A person signed.",
      run: async () => {
        unspotlight();
        await goto("activity");
        await spotlight(".timeline");
        mustContain("#content", "control.approved", "the audit chain is empty");
      },
    },
    {
      say:
        "Point it at a manufacturer, and the taxonomy is different, because discovery is reading " +
        "a different estate.",
      run: async () => {
        unspotlight();
        const select = document.querySelector("#tenant-select");
        select.value = "brandt";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await sleep(1400);
        await goto("domains");
        await waitFor("table.table tbody tr");
        const codes = [...document.querySelectorAll("tbody tr td:first-child")].map((cell) =>
          cell.innerText.trim()
        );
        if (!codes.includes("HSE") || !codes.includes("TRADE")) {
          fail(`the manufacturer's taxonomy did not load: ${codes.join(", ")}`);
        }
        await spotlight("table.table");
      },
    },
    {
      say:
        "Health and safety. Export control. Sustainability disclosure. The same product and the " +
        "same gates, over a different company.",
      run: async () => {
        await sleep(400);
      },
    },
    {
      say: "Countersign. The walking is automatic. The signature stays with a person.",
      run: async () => {
        unspotlight();
        await showCard("The walking is automatic", "The signature stays with a person");
        await sleep(700);
      },
    },
  ];

  // ---------------------------------------------------------------- driver

  function secondsFor(index) {
    if (timing && timing[index]) return timing[index];
    const words = beats[index].say.split(/\s+/).length;
    return Math.max(3.2, words / 2.6 + 1.1);
  }

  async function play() {
    // The gates are part of the demonstration, so it signs in through the same
    // endpoint the console uses.
    try {
      await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ username: "risk", password: "countersign" }),
      });
      if (window.Countersign) await window.Countersign.refresh();
    } catch (error) {
      fail(`could not sign in: ${error}`);
    }

    await sleep(900);
    for (let index = 0; index < beats.length; index += 1) {
      if (state.stopped) break;
      state.index = index;
      const beat = beats[index];
      const started = performance.now();
      try {
        await beat.run();
      } catch (error) {
        fail(`threw: ${error && error.message}`);
      }
      if (!card.classList.contains("on")) say(beat.say);
      const elapsed = (performance.now() - started) / 1000;
      const budget = secondsFor(index);
      if (elapsed > budget + 1.5) {
        fail(`overran its narration by ${(elapsed - budget).toFixed(1)}s`);
      }
      await sleep(Math.max(600, (budget - elapsed) * 1000));
    }
    hush();
    unspotlight();
    cursor.classList.remove("on");
    state.finished = true;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      state.stopped = true;
      hush();
      unspotlight();
      card.classList.remove("on");
      cursor.classList.remove("on");
    }
  });

  state.beats = beats;
  state.captions = beats.map((beat) => beat.say);
  window.CountersignDemo = state;

  const start = () => {
    state.ready = true;
    if (!manual) play();
  };

  if (document.readyState === "complete") setTimeout(start, 800);
  else window.addEventListener("load", () => setTimeout(start, 800));
})();

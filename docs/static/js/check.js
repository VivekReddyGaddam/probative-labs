/* Probative Labs free check: did the virtual agent's "resolved" conversations hold?
 *
 * Everything runs in this browser. The page's Content-Security-Policy (connect-src 'none') stops it from
 * sending anything anywhere: open the browser's Network tab while it runs and you will see no requests.
 *
 * The definitions follow the open engine's resolution audit (`probative audit --claims-preset servicenow_va`):
 * a claimed resolution came back if the same person opened a ticket within the window after the conversation
 * started; the normal rate is how often people came back after tickets your agents resolved, in the same
 * months; only the excess counts against the AI:
 *   did not hold = (came back after the AI − came back after people) ÷ (1 − came back after people)
 */
const ProbativeCheck = (() => {
  "use strict";

  const DAY = 86400000;
  const MINUTE = 60000;
  const MIN_CELL = 30;
  // As in the engine: a ticket the AI agent opens itself (ServiceNow's Create Incident topic sets Contact type to
  // Virtual Agent) is a handoff, not a return. One through these channels within LINK_MINUTES of the same
  // person's latest conversation was created in that conversation.
  const AI_CHANNELS = "virtual agent, ai agent, chatbot, bot";
  const LINK_MINUTES = 30;

  // --- CSV ---------------------------------------------------------------------------------------------
  function parseCSV(text) {
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const nl = text.indexOf("\n");
    const first = nl === -1 ? text : text.slice(0, nl);
    let delim = ",";
    for (const d of [";", "\t"]) if (first.split(d).length > first.split(delim).length) delim = d;
    const rows = [];
    let row = [];
    let i = 0;
    const n = text.length;
    while (i <= n) {
      if (i === n) {
        if (row.length) rows.push(row);
        break;
      }
      let field = "";
      if (text[i] === '"') {
        i++;
        let start = i;
        for (;;) {
          const q = text.indexOf('"', i);
          if (q === -1) {
            field += text.slice(start);
            i = n;
            break;
          }
          if (text[q + 1] === '"') {
            field += text.slice(start, q + 1);
            i = q + 2;
            start = i;
            continue;
          }
          field += text.slice(start, q);
          i = q + 1;
          break;
        }
        while (i < n && text[i] !== delim && text[i] !== "\n") i++;
      } else {
        const start = i;
        while (i < n && text[i] !== delim && text[i] !== "\n") i++;
        field = text.slice(start, i);
      }
      if (field.endsWith("\r")) field = field.slice(0, -1);
      row.push(field);
      if (i >= n) {
        rows.push(row);
        break;
      }
      if (text[i] === "\n") {
        rows.push(row);
        row = [];
      }
      i++;
    }
    const header = (rows.shift() || []).map((h) => h.trim());
    return { header, rows: rows.filter((r) => r.length > 1 || (r[0] || "").trim() !== "") };
  }

  // --- Columns -------------------------------------------------------------------------------------------
  const norm = (v) =>
    String(v == null ? "" : v)
      .trim()
      .toLowerCase()
      .replace(/[_\s]+/g, " ");

  const CONVERSATION_FIELDS = {
    person: ["opened_for.sys_id", "opened_for", "opened for", "consumer", "requester_id", "requester", "caller_id",
      "user", "user id", "user email", "email"],
    started: ["opened_at", "opened", "sys_created_on", "created", "started_at", "start time", "created at"],
    state: ["state"],
    reason: ["state_reason", "state reason"],
    agentChat: ["agent_chat", "agent chat"],
    virtualAgent: ["virtual_agent", "virtual agent"],
    type: ["type"],
    outcome: ["outcome", "resolution state", "deflection outcome", "fin ai agent resolution state"],
  };
  // Column names from the engine's export presets: ServiceNow, Jira Service Management, Zendesk, Freshservice,
  // BMC Helix and Ivanti. The first name found wins; anything else is matched by hand on the page.
  const TICKET_FIELDS = {
    id: ["number", "issue key", "ticket id", "id", "sys_id", "incident number", "incident id", "incident id*+",
      "incidentnumber", "incident #"],
    person: ["caller_id.sys_id", "caller_id", "caller", "opened_for.sys_id", "opened_for", "requester_id", "requester",
      "requester email", "reporter", "requester name", "email", "person id", "customer login id",
      "direct contact login id", "profilelink_recid", "profilelink", "profilefullname", "customer"],
    opened: ["opened_at", "opened", "sys_created_on", "created", "created at", "created date", "created time",
      "reported date", "reported date+", "submit date", "createddatetime", "created date time", "created on"],
    resolved: ["resolved_at", "resolved", "resolution date", "resolved at", "solved at", "resolved date", "resolved time",
      "last resolved date", "resolveddatetime", "resolved date time", "resolved on"],
    category: ["category", "u_category", "request type", "custom field (request type)",
      "custom field (customer request type)", "issue type", "ticket type", "operational categorization tier 1",
      "categorization tier 1", "operational categorization tier 1+", "actualcategory", "type"],
    channel: ["contact_type", "contact type", "channel", "via", "source", "reported source", "reported source+"],
    reopens: ["reopen_count", "reopen count", "reopens", "reopened"],
  };

  function detect(header, fields) {
    const lookup = new Map();
    header.forEach((h, i) => {
      if (!lookup.has(norm(h))) lookup.set(norm(h), i);
    });
    const out = {};
    for (const [key, candidates] of Object.entries(fields)) {
      out[key] = -1;
      for (const c of candidates) {
        if (lookup.has(norm(c))) {
          out[key] = lookup.get(norm(c));
          break;
        }
      }
    }
    return out;
  }

  // --- Dates ---------------------------------------------------------------------------------------------
  const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
  const NUMERIC = /^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?/;
  const ISO = /^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/;
  const NAMED = /^\s*(\d{1,2})[/\- ]([A-Za-z]{3})[a-z]*[/\- ](\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?/;

  // Exports follow the user's date preference, so one file is consistent: the clear cases decide.
  function dayFirst(values) {
    let d = 0;
    let m = 0;
    for (const v of values) {
      const x = NUMERIC.exec(v);
      if (!x) continue;
      if (+x[1] > 12) d++;
      if (+x[2] > 12) m++;
      if (d + m > 200) break;
    }
    return d > 0 && m === 0;
  }

  function clock(h, mi, s, ampm) {
    let hour = +(h || 0);
    if (ampm) {
      const pm = ampm.toLowerCase() === "pm";
      if (pm && hour < 12) hour += 12;
      if (!pm && hour === 12) hour = 0;
    }
    return [hour, +(mi || 0), +(s || 0)];
  }

  function parseDate(v, df) {
    if (v == null) return NaN;
    const s = String(v).trim();
    if (!s) return NaN;
    let x = ISO.exec(s);
    if (x) return Date.UTC(+x[1], +x[2] - 1, +x[3], ...clock(x[4], x[5], x[6]));
    x = NUMERIC.exec(s);
    if (x) {
      const day = df ? +x[1] : +x[2];
      const month = df ? +x[2] : +x[1];
      let year = +x[3];
      if (year < 100) year += 2000;
      return Date.UTC(year, month - 1, day, ...clock(x[4], x[5], x[6], x[7]));
    }
    x = NAMED.exec(s);
    if (x && MONTHS[x[2].toLowerCase()]) {
      let year = +x[3];
      if (year < 100) year += 2000;
      return Date.UTC(year, MONTHS[x[2].toLowerCase()] - 1, +x[1], ...clock(x[4], x[5], x[6], x[7]));
    }
    const t = Date.parse(s);
    return Number.isNaN(t) ? NaN : t;
  }

  const monthOf = (t) => {
    const d = new Date(t);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  };

  // --- Outcomes ------------------------------------------------------------------------------------------
  const truthy = (v) => ["true", "1", "yes", "y", "t"].includes(norm(v));

  function readable(v) {
    const s = String(v == null ? "" : v).trim();
    if (/^[a-z0-9_]+$/.test(s)) return s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
    return s;
  }

  // ServiceNow closes a Virtual Agent chat the person walked away from as "Closed Complete": that is what its
  // reporting counts as handled, so it is a claimed resolution here too (as in the engine's servicenow_va preset).
  function serviceNowOutcome(state, reason, live) {
    const st = norm(state);
    const rs = norm(reason);
    if (live || rs === "agent ended" || rs === "left before engagement") return "escalated";
    if (["in progress", "new", "work in progress"].includes(st)) return "open";
    if (st === "closed abandoned" || rs === "no activity" || st === "faulted" || rs === "error") return "abandoned";
    if (st === "closed complete") return "resolved";
    return "other";
  }

  function genericOutcome(value, resolvedValues) {
    const o = norm(value);
    if (!o) return "other";
    if (resolvedValues.has(o)) return "resolved";
    if (["open", "in progress", "new", "active"].includes(o)) return "open";
    if (/abandon|no response|no activity|timed? ?out|unanswered|dropped/.test(o)) return "abandoned";
    if (/escalat|hand ?off|handed|transfer|routed|live agent|human/.test(o)) return "escalated";
    return "other";
  }

  function conversations(table, cols, resolvedValue) {
    const resolvedValues = new Set(
      String(resolvedValue || "resolved")
        .split(",")
        .map((v) => norm(v))
        .filter(Boolean),
    );
    const serviceNow = cols.state >= 0;
    if (serviceNow && cols.agentChat < 0) {
      throw new Error(
        "This ServiceNow export has no Agent chat column. Without it, chats a live agent took over would count as " +
          "resolved, and the incidents those agents opened as returns. Export the Interactions list with Agent chat, " +
          "or pick its column under the conversations file.",
      );
    }
    const starts = table.rows.map((r) => r[cols.started]);
    const df = dayFirst(starts);
    const out = [];
    let skipped = 0;
    for (const r of table.rows) {
      if (serviceNow && cols.virtualAgent >= 0 && !truthy(r[cols.virtualAgent])) {
        skipped++;
        continue;
      }
      // With the Virtual agent flag, it alone says which rows were the bot's (Teams or Slack may not be typed
      // "chat"); without it, keep the chats. As in the engine.
      if (serviceNow && cols.virtualAgent < 0 && cols.type >= 0 && !["chat", ""].includes(norm(r[cols.type]))) {
        skipped++;
        continue;
      }
      const live = cols.agentChat >= 0 && truthy(r[cols.agentChat]);
      let outcome;
      let detail;
      if (serviceNow) {
        outcome = serviceNowOutcome(r[cols.state], cols.reason >= 0 ? r[cols.reason] : "", live);
        detail = live ? "Live agent joined" : readable(cols.reason >= 0 && r[cols.reason] ? r[cols.reason] : r[cols.state]);
      } else if (cols.outcome >= 0) {
        outcome = genericOutcome(r[cols.outcome], resolvedValues);
        detail = readable(r[cols.outcome]);
      } else {
        outcome = "resolved";
        detail = "";
      }
      out.push({
        person: String(r[cols.person] == null ? "" : r[cols.person]).trim(),
        start: parseDate(r[cols.started], df),
        outcome,
        detail,
      });
    }
    return { rows: out, skipped, serviceNow };
  }

  // As the engine's health check: a ticket number seen twice keeps its first row, and a ticket resolved
  // before it was opened is left out.
  function tickets(table, cols, notes) {
    const df = dayFirst(table.rows.map((r) => r[cols.opened]).concat(cols.resolved >= 0 ? table.rows.map((r) => r[cols.resolved]) : []));
    const seen = new Set();
    let duplicates = 0;
    let impossible = 0;
    const out = [];
    for (const r of table.rows) {
      if (cols.id >= 0) {
        const key = String(r[cols.id] == null ? "" : r[cols.id]).trim();
        if (key && seen.has(key)) {
          duplicates++;
          continue;
        }
        seen.add(key);
      }
      const t = {
        person: String(r[cols.person] == null ? "" : r[cols.person]).trim(),
        opened: parseDate(r[cols.opened], df),
        resolved: cols.resolved >= 0 ? parseDate(r[cols.resolved], df) : NaN,
        category: cols.category >= 0 ? String(r[cols.category] || "").trim() : "",
        channel: cols.channel >= 0 ? String(r[cols.channel] == null ? "" : r[cols.channel]).trim() : "",
        reopened: cols.reopens >= 0 && (parseFloat(r[cols.reopens]) > 0 || truthy(r[cols.reopens])),
      };
      if (!Number.isNaN(t.resolved) && !Number.isNaN(t.opened) && t.resolved < t.opened) {
        impossible++;
        continue;
      }
      out.push(t);
    }
    if (duplicates) notes.push(`${duplicates.toLocaleString()} duplicate ticket numbers were left out (the first row was kept).`);
    if (impossible) notes.push(`${impossible.toLocaleString()} tickets resolved before they were opened were left out.`);
    return out;
  }

  // --- The check -----------------------------------------------------------------------------------------
  function poisson(rand) {
    const L = Math.exp(-1);
    let k = 0;
    let p = 1;
    do {
      k++;
      p *= rand();
    } while (p > L);
    return k - 1;
  }

  function mulberry32(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const failed = (r, b) => (b < 1 ? (r - b) / (1 - b) : NaN);

  // The human baseline standardized to the claims' months: each month of claims is compared with tickets
  // your agents resolved that month; a month with fewer than 30 falls back to all of them.
  function standardized(claimCells, baseCells, basePooled) {
    let n = 0;
    let s = 0;
    let b = 0;
    for (const [month, c] of claimCells) {
      if (!c.n) continue;
      const cell = baseCells.get(month);
      const rate = cell && cell.n >= MIN_CELL ? cell.s / cell.n : basePooled;
      n += c.n;
      s += c.s;
      b += c.n * rate;
    }
    if (!n) return { ai: NaN, base: NaN, failed: NaN, n: 0 };
    return { ai: s / n, base: b / n, failed: failed(s / n, b / n), n };
  }

  function run(convTable, convCols, ticketTable, ticketCols, opts = {}) {
    const windowDays = opts.windowDays || 7;
    const W = windowDays * DAY;
    const reps = opts.reps || 400;
    const notes = [];
    const warnings = []; // data problems to fix before trusting the result: shown above it
    const linkMinutes =
      opts.linkMinutes == null || opts.linkMinutes === "" || Number.isNaN(+opts.linkMinutes)
        ? LINK_MINUTES
        : Math.max(0, +opts.linkMinutes);
    const LINK = linkMinutes * MINUTE;
    if (LINK >= W) throw new Error("The minutes for tickets the AI agent created must be shorter than the return window.");
    const aiChannels = new Set(
      String(opts.aiChannels == null ? AI_CHANNELS : opts.aiChannels)
        .split(",")
        .map((v) => norm(v))
        .filter(Boolean),
    );

    const conv = conversations(convTable, convCols, opts.resolvedValue);
    if (conv.skipped) notes.push(`${conv.skipped.toLocaleString()} rows without the Virtual agent flag, or not chats, were left out.`);
    if (conv.serviceNow && convCols.virtualAgent < 0) {
      notes.push(
        "The conversations have no Virtual agent column, so every row is taken to be a Virtual Agent conversation. " +
          "Filter the Interactions list to Virtual agent = true before exporting.",
      );
    }
    const tk = tickets(ticketTable, ticketCols, notes).filter((t) => t.person && !Number.isNaN(t.opened));
    if (!tk.length) throw new Error("No tickets with a person and an opened date were found. Check the columns.");

    let dataStart = Infinity;
    let dataEnd = -Infinity;
    const byPerson = new Map();
    tk.forEach((t, i) => {
      if (t.opened < dataStart) dataStart = t.opened;
      if (t.opened > dataEnd) dataEnd = t.opened;
      if (!byPerson.has(t.person)) byPerson.set(t.person, []);
      byPerson.get(t.person).push(i);
    });
    for (const list of byPerson.values()) list.sort((a, b) => tk[a].opened - tk[b].opened);

    // The ticket the same person opened next, within the window after time t (strictly after, as in the engine).
    // With "after", only tickets opened later than that after t count.
    function nextTicket(person, t, after = 0) {
      const list = byPerson.get(person);
      if (!list) return -1;
      const from = t + after;
      let lo = 0;
      let hi = list.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (tk[list[mid]].opened > from) hi = mid;
        else lo = mid + 1;
      }
      if (lo < list.length && tk[list[lo]].opened - t <= W) return list[lo];
      return -1;
    }

    const outcomes = { resolved: 0, escalated: 0, abandoned: 0, open: 0, other: 0 };
    conv.rows.forEach((c) => {
      outcomes[c.outcome] = (outcomes[c.outcome] || 0) + 1;
    });
    // Tickets the AI agent created itself. With the ticket's channel they are found exactly, and the
    // conversations that created them are handoffs, left out. Without it, a ticket opened in the first minutes
    // could be either, so on both sides only tickets opened after LINK count as returns.
    const channelKnown = ticketCols.channel >= 0 && tk.some((t) => t.channel);
    const after = channelKnown ? 0 : LINK;
    const key = (person, start) => `${person}\u0000${start}`;
    const handoffKeys = new Set();
    if (channelKnown && LINK > 0) {
      const starts = new Map();
      for (const c of conv.rows) {
        if (!c.person || Number.isNaN(c.start)) continue;
        if (!starts.has(c.person)) starts.set(c.person, []);
        starts.get(c.person).push(c.start);
      }
      for (const list of starts.values()) list.sort((a, b) => a - b);
      let first = Infinity;
      let last = -Infinity;
      for (const c of conv.rows) {
        if (Number.isNaN(c.start)) continue;
        if (c.start < first) first = c.start;
        if (c.start > last) last = c.start;
      }
      let aiTickets = 0;
      let linked = 0;
      for (const t of tk) {
        if (!aiChannels.has(norm(t.channel))) continue;
        const during = t.opened >= first && t.opened <= last + LINK;
        if (during) aiTickets++;
        const list = starts.get(t.person);
        if (!list) continue;
        let lo = 0;
        let hi = list.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (list[mid] <= t.opened) lo = mid + 1;
          else hi = mid;
        }
        if (lo > 0 && t.opened - list[lo - 1] <= LINK) {
          handoffKeys.add(key(t.person, list[lo - 1]));
          if (during) linked++;
        }
      }
      // Tickets through the AI's channel that match no conversation usually mean the exports disagree on time
      // zone or person IDs; then the AI's own tickets would be counted as returns.
      if (aiTickets >= 10 && linked < 0.5 * aiTickets) {
        warnings.push(
          `${(aiTickets - linked).toLocaleString()} of ${aiTickets.toLocaleString()} tickets through the AI agent's channel ` +
            `had no conversation from the same person in the ${linkMinutes} minutes before. If the two exports use ` +
            "different time zones or person IDs, fix that first: those tickets are being counted as returns.",
        );
      }
    }
    const allClaims = conv.rows.filter((c) => c.outcome === "resolved" && c.person && !Number.isNaN(c.start));
    const claims = allClaims.filter((c) => !handoffKeys.has(key(c.person, c.start)));
    const handoffs = allClaims.length - claims.length;
    const outside = claims.filter((c) => c.start < dataStart || c.start > dataEnd).length;
    const tooRecent = claims.filter((c) => c.start >= dataStart && c.start <= dataEnd && c.start > dataEnd - W).length;
    const eligible = claims.filter((c) => c.start >= dataStart && c.start <= dataEnd - W);
    if (!eligible.length) {
      throw new Error(
        "None of the resolved conversations fall inside the ticket export with a week to follow up. " +
          "Export tickets covering the same months, plus a week.",
      );
    }
    let lo = Infinity;
    let hi = -Infinity;
    for (const c of eligible) {
      c.next = nextTicket(c.person, c.start, after);
      c.ret = c.next >= 0 ? 1 : 0;
      c.month = monthOf(c.start);
      if (c.start < lo) lo = c.start;
      if (c.start > hi) hi = c.start;
    }
    const base = [];
    for (let i = 0; i < tk.length; i++) {
      const t = tk[i];
      if (Number.isNaN(t.resolved) || t.resolved < lo || t.resolved > hi) continue;
      base.push({
        person: t.person,
        ret: nextTicket(t.person, t.resolved, after) >= 0 ? 1 : 0,
        month: monthOf(t.resolved),
        reopened: t.reopened,
      });
    }
    if (!base.length) throw new Error("No tickets resolved by your agents in the same weeks. Include the resolved date.");

    // Point estimate.
    const cells = (items, weight) => {
      const m = new Map();
      items.forEach((x, i) => {
        const w = weight ? weight[i] : 1;
        if (!w) return;
        const c = m.get(x.month) || { n: 0, s: 0 };
        c.n += w;
        c.s += w * x.ret;
        m.set(x.month, c);
      });
      return m;
    };
    const pooledOf = (items, weight) => {
      let n = 0;
      let s = 0;
      items.forEach((x, i) => {
        const w = weight ? weight[i] : 1;
        n += w;
        s += w * x.ret;
      });
      return n ? s / n : NaN;
    };
    const point = standardized(cells(eligible), cells(base), pooledOf(base));

    // Range: resample people (a Poisson bootstrap), so one person's contacts move together.
    const people = new Map();
    const pid = (p) => {
      if (!people.has(p)) people.set(p, people.size);
      return people.get(p);
    };
    const claimPeople = eligible.map((c) => pid(c.person));
    const basePeople = base.map((b) => pid(b.person));
    const rand = mulberry32(20261014);
    const draws = [];
    const weightsOfPeople = new Float64Array(people.size);
    for (let r = 0; r < reps; r++) {
      for (let p = 0; p < weightsOfPeople.length; p++) weightsOfPeople[p] = poisson(rand);
      const wc = claimPeople.map((p) => weightsOfPeople[p]);
      const wb = basePeople.map((p) => weightsOfPeople[p]);
      const est = standardized(cells(eligible, wc), cells(base, wb), pooledOf(base, wb));
      if (!Number.isNaN(est.failed)) draws.push(est.failed);
    }
    draws.sort((a, b) => a - b);
    const q = (p) => (draws.length ? draws[Math.min(draws.length - 1, Math.max(0, Math.round(p * (draws.length - 1))))] : NaN);
    const range = { low: q(0.025), high: q(0.975) };

    // How much the result depends on two choices: whose resolutions make the baseline, and the tickets the AI
    // agent may have created itself (the engine's sensitivity checks).
    const checks = [{ check: "Main result", didNotHold: point.failed, main: true }];
    const peopleWithClaims = new Set(eligible.map((c) => c.person));
    const baseSame = base.filter((b) => peopleWithClaims.has(b.person));
    if (baseSame.length >= MIN_CELL) {
      const s = standardized(cells(eligible), cells(baseSame), pooledOf(baseSame));
      checks.push({ check: "Baseline from the same people only", didNotHold: s.failed });
    }
    if (base.some((b) => b.reopened)) {
      // After your agents, a person whose problem wasn't fixed often reopens the ticket instead of opening a new
      // one; after the AI there is nothing to reopen. Counting reopens on that side shows how much that matters.
      const baseR = base.map((b) => ({ person: b.person, month: b.month, ret: b.ret || b.reopened ? 1 : 0 }));
      const s = standardized(cells(eligible), cells(baseR), pooledOf(baseR));
      checks.push({ check: "Reopened tickets count as returns", didNotHold: s.failed });
    }
    if (handoffs || (!channelKnown && LINK > 0)) {
      let lo2 = Infinity;
      let hi2 = -Infinity;
      const everyClaim = [];
      for (const c of allClaims) {
        if (c.start < dataStart || c.start > dataEnd - W) continue;
        everyClaim.push({ month: monthOf(c.start), ret: nextTicket(c.person, c.start) >= 0 ? 1 : 0 });
        if (c.start < lo2) lo2 = c.start;
        if (c.start > hi2) hi2 = c.start;
      }
      const everyBase = [];
      for (const t of tk) {
        if (Number.isNaN(t.resolved) || t.resolved < lo2 || t.resolved > hi2) continue;
        everyBase.push({ month: monthOf(t.resolved), ret: nextTicket(t.person, t.resolved) >= 0 ? 1 : 0 });
      }
      if (everyClaim.length && everyBase.length) {
        const s = standardized(cells(everyClaim), cells(everyBase), pooledOf(everyBase));
        checks.push({ check: "Every ticket counts, even ones the AI agent may have created", didNotHold: s.failed });
      }
    }

    const perPerson = new Map();
    for (const c of eligible) perPerson.set(c.person, (perPerson.get(c.person) || 0) + 1);
    let topCount = 0;
    for (const n of perPerson.values()) if (n > topCount) topCount = n;
    const topShare = eligible.length ? topCount / eligible.length : 0;
    if (eligible.length >= 100 && topShare > 0.05) {
      warnings.push(
        `One person accounts for ${Math.round(topShare * 100)}% of the conversations checked. If it's a test or shared ` +
          "account, leave it out of the export: one person can sway the result.",
      );
    }
    if (handoffs) {
      notes.push(
        `${handoffs.toLocaleString()} conversations counted as resolved created a ticket themselves: a ticket through ` +
          `the AI agent's channel within ${linkMinutes} minutes. They were handed to your team, so they are left out ` +
          "instead of being counted as returns. If your dashboard counts them as resolved, its resolution rate includes them.",
      );
    }
    let fastChannels = [];
    if (channelKnown && LINK > 0) {
      const counts = new Map();
      let total = 0;
      for (const c of eligible) {
        const list = byPerson.get(c.person);
        if (!list) continue;
        let lo = 0;
        let hi = list.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (tk[list[mid]].opened > c.start) hi = mid;
          else lo = mid + 1;
        }
        if (lo >= list.length) continue;
        const t = tk[list[lo]];
        if (t.opened - c.start > LINK || aiChannels.has(norm(t.channel))) continue;
        const label = t.channel || "(blank)";
        counts.set(label, (counts.get(label) || 0) + 1);
        total++;
      }
      if (total >= Math.max(10, 0.01 * eligible.length)) {
        fastChannels = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
        notes.push(
          `Tickets opened within ${linkMinutes} minutes of a conversation counted as resolved came through ` +
            fastChannels.map(([c, n]) => `${c} (${n.toLocaleString()})`).join(", ") +
            ". If the AI agent opens tickets through one of these channels, add it under Options, so those tickets " +
            "count as handoffs, not returns. Some may come from topics that send people to the desk on purpose: " +
            "those conversations were never resolutions, whatever the AI's reporting says.",
        );
      }
    } else if (LINK > 0) {
      notes.push(
        "The incidents have no channel column, so a ticket the AI agent created during a conversation can't be told " +
          `apart from a person coming straight back. Returns count from ${linkMinutes} minutes after each conversation ` +
          "or resolution, on both sides. Add Contact type to the incidents export to count faster returns too.",
      );
    }

    // How the platform closed the conversations it counted as resolved.
    const details = new Map();
    for (const c of eligible) {
      const d = details.get(c.detail) || { n: 0, s: 0 };
      d.n++;
      d.s += c.ret;
      details.set(c.detail, d);
    }
    const byDetail =
      details.size >= 2
        ? [...details.entries()]
            .filter(([, d]) => d.n >= MIN_CELL)
            .map(([detail, d]) => ({
              detail,
              claims: d.n,
              share: d.n / eligible.length,
              cameBack: d.s / d.n,
              didNotHold: failed(d.s / d.n, point.base),
            }))
            .sort((a, b) => b.claims - a.claims)
        : [];

    // What the people who came back opened tickets about: the bot's fix list.
    const cats = new Map();
    let returns = 0;
    for (const c of eligible) {
      if (c.next < 0) continue;
      returns++;
      const cat = tk[c.next].category || "(no category)";
      cats.set(cat, (cats.get(cat) || 0) + 1);
    }
    const cameBackAbout = ticketCols.category >= 0
      ? [...cats.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([category, n]) => ({ category, returns: n, share: n / returns }))
      : [];

    // By month.
    const claimMonths = cells(eligible);
    const baseMonths = cells(base);
    const pooled = pooledOf(base);
    const byMonth = [...claimMonths.entries()]
      .filter(([, c]) => c.n >= MIN_CELL)
      .sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .map(([month, c]) => {
        const bc = baseMonths.get(month);
        const b = bc && bc.n >= MIN_CELL ? bc.s / bc.n : pooled;
        return { month, claims: c.n, cameBack: c.s / c.n, afterPeople: b, didNotHold: failed(c.s / c.n, b) };
      });

    const ticketPeople = new Set(tk.map((t) => t.person));
    const overlap = eligible.filter((c) => ticketPeople.has(c.person)).length / eligible.length;
    if (overlap < 0.5) {
      warnings.push(
        `Only ${Math.round(overlap * 100)}% of the people in the conversations appear in the tickets, so the two ` +
          "files probably identify people differently (for example a name in one and an ID in the other).",
      );
    }
    if (!conv.serviceNow && convCols.outcome < 0) notes.push("The conversations have no outcome column, so every row counted as a resolution.");
    const caveat =
      "A screening estimate, not a verdict: any ticket from the same person counts as a return, and the comparison " +
      "is with all of your agents' resolutions. The full audit also matches the type of issue, runs more checks, and " +
      "widens the range with a month-by-month check.";

    const fmtDay = (t) => new Date(t).toISOString().slice(0, 10);
    return {
      version: "free-check-2",
      windowDays,
      linkMinutes,
      channelKnown,
      period: { from: fmtDay(lo), to: fmtDay(hi) },
      conversations: { rows: conv.rows.length, outcomes, serviceNow: conv.serviceNow },
      claims: {
        total: allClaims.length,
        handedOff: handoffs,
        checked: eligible.length,
        tooRecent,
        outside,
        people: new Set(eligible.map((c) => c.person)).size,
      },
      tickets: { rows: tk.length, baselineResolved: base.length },
      cameBack: point.ai,
      afterPeople: point.base,
      didNotHold: point.failed,
      range,
      held: Math.min(eligible.length, eligible.length * (1 - Math.max(point.failed, 0))),
      byDetail,
      cameBackAbout,
      byMonth,
      overlap,
      checks,
      fastChannels,
      caveat,
      warnings,
      notes,
    };
  }

  return { parseCSV, detect, CONVERSATION_FIELDS, TICKET_FIELDS, run, parseDate, dayFirst };
})();

if (typeof module === "object" && module.exports) {
  module.exports = ProbativeCheck;
} else {
  (function ui() {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const root = $("free-check");
    if (!root) return;
    const state = { conv: null, tickets: null, sample: false };
    const pct = (x, d = 0) => (x == null || Number.isNaN(x) ? "n/a" : `${(x * 100).toFixed(d)}%`.replace("-", "−"));
    const num = (x) => (x == null || Number.isNaN(x) ? "n/a" : Math.round(x).toLocaleString());

    function el(tag, attrs = {}, ...children) {
      const node = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") node.className = v;
        else node.setAttribute(k, v);
      }
      for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
      return node;
    }

    const LABELS = {
      person: "Person",
      started: "Conversation start",
      state: "State",
      reason: "State reason",
      agentChat: "Agent chat",
      virtualAgent: "Virtual agent",
      type: "Type",
      outcome: "Outcome",
      opened: "Opened",
      resolved: "Resolved",
      id: "Ticket number",
      category: "Category",
      channel: "Channel (Contact type)",
      reopens: "Reopen count",
    };
    const REQUIRED = { conv: ["person", "started"], tickets: ["person", "opened", "resolved"] };

    function mappingTable(kind, table, cols, fields) {
      const wrap = el("div", { class: "check-map" });
      for (const key of Object.keys(fields)) {
        const select = el("select", { "data-kind": kind, "data-key": key, "aria-label": LABELS[key] });
        select.append(el("option", { value: "-1" }, "(none)"));
        table.header.forEach((h, i) => {
          const o = el("option", { value: String(i) }, h || `(column ${i + 1})`);
          if (i === cols[key]) o.selected = true;
          select.append(o);
        });
        select.addEventListener("change", () => {
          cols[key] = Number(select.value);
          ready();
        });
        const need = REQUIRED[kind].includes(key) || (kind === "conv" && key === "agentChat" && cols.state >= 0);
        wrap.append(el("label", { class: "check-field" }, el("span", {}, LABELS[key] + (need ? "" : " (optional)")), select));
      }
      return wrap;
    }

    async function load(kind, file) {
      const status = $(`${kind}-status`);
      status.classList.remove("error");
      status.textContent = `Reading ${file.name}…`;
      const text = await file.text();
      const table = ProbativeCheck.parseCSV(text);
      if (!table.header.length || !table.rows.length) {
        throw new Error(`${file.name} has no rows to read. Export the list as CSV, with its column headers.`);
      }
      // A file in the wrong box: incidents have a caller and a resolved date; conversations have neither.
      const has = (names) => table.header.some((h) => names.includes(String(h).trim().toLowerCase().replace(/[_\s]+/g, " ")));
      const looksLikeIncidents = has(["caller id", "caller", "resolved at", "resolved"]) && !has(["agent chat", "state reason", "opened for"]);
      const looksLikeConversations = has(["agent chat", "state reason"]) && !has(["caller id", "caller", "resolved at", "resolved"]);
      if (kind === "conv" && looksLikeIncidents) {
        throw new Error(`${file.name} looks like the incidents export. Drop it in box 2, and the conversations in box 1.`);
      }
      if (kind === "tickets" && looksLikeConversations) {
        throw new Error(`${file.name} looks like the conversations export. Drop it in box 1, and the incidents in box 2.`);
      }
      const fields = kind === "conv" ? ProbativeCheck.CONVERSATION_FIELDS : ProbativeCheck.TICKET_FIELDS;
      const cols = ProbativeCheck.detect(table.header, fields);
      state[kind] = { table, cols, fields, name: file.name };
      $(`${kind}-name`).textContent = file.name;
      $(`${kind}-zone`).classList.add("chosen");
      status.textContent = `${table.rows.length.toLocaleString()} rows, ${table.header.length} columns. Check the columns below.`;
      const holder = $(`${kind}-map`);
      holder.replaceChildren(mappingTable(kind, table, cols, fields));
      ready();
    }

    function failed(kind, error) {
      state[kind] = null;
      $(`${kind}-zone`).classList.remove("chosen");
      $(`${kind}-map`).replaceChildren();
      const status = $(`${kind}-status`);
      status.classList.add("error");
      status.textContent = error.message;
      ready();
    }

    function ready() {
      const ok =
        state.conv &&
        state.tickets &&
        REQUIRED.conv.every((k) => state.conv.cols[k] >= 0) &&
        REQUIRED.tickets.every((k) => state.tickets.cols[k] >= 0);
      $("check-run").disabled = !ok;
      return ok;
    }

    function table(head, rows) {
      const t = el("table");
      t.append(el("thead", {}, el("tr", {}, ...head.map((h, i) => el("th", i ? { class: "n" } : {}, h)))));
      const body = el("tbody");
      for (const r of rows) body.append(el("tr", {}, ...r.map((c, i) => el("td", i ? { class: "n" } : {}, c))));
      t.append(body);
      return el("div", { class: "table-wrap" }, t);
    }

    function render(r) {
      const out = $("check-results");
      const headline =
        `Of ${num(r.claims.checked)} conversations counted as resolved (${r.period.from} to ${r.period.to}), ` +
        `${pct(r.cameBack, 1)} came back as a ticket within ${r.windowDays} days. After your agents' own resolutions, ` +
        `${pct(r.afterPeople, 1)} did. Beyond that normal rate, ${pct(r.didNotHold, 1)} did not hold ` +
        `(95% range ${pct(r.range.low, 1)} to ${pct(r.range.high, 1)}).` +
        (r.claims.handedOff ? ` ${num(r.claims.handedOff)} more created a ticket themselves, so they were left out.` : "");
      const blocks = [
        el("h2", {}, "Result", el("span", { class: "check-badge" }, "Screening estimate")),
        ...(state.sample
          ? [el("p", { class: "callout warn" }, "Example only: made-up data from a simulated service desk, to show what you'll get. Drop your own exports above to check yours.")]
          : []),
        ...(r.warnings.length
          ? [el("div", { class: "callout warn" }, el("b", {}, "Check this before you rely on the result."), el("ul", {}, ...r.warnings.map((w) => el("li", {}, w))))]
          : []),
        el("p", { class: "headline" }, headline),
        el("p", { class: "small muted" }, r.caveat),
        el(
          "div",
          { class: "tiles" },
          el("div", { class: "tile" }, el("div", { class: "k" }, "Counted as resolved"), el("div", { class: "v" }, num(r.claims.checked)), el("div", { class: "s" }, `${num(r.claims.people)} people`)),
          el("div", { class: "tile" }, el("div", { class: "k" }, "Came back"), el("div", { class: "v" }, pct(r.cameBack, 1)), el("div", { class: "s" }, `within ${r.windowDays} days`)),
          el("div", { class: "tile" }, el("div", { class: "k" }, "After your agents"), el("div", { class: "v" }, pct(r.afterPeople, 1)), el("div", { class: "s" }, `${num(r.tickets.baselineResolved)} tickets`)),
          el("div", { class: "tile" }, el("div", { class: "k" }, "Did not hold"), el("div", { class: "v" }, pct(r.didNotHold, 1)), el("div", { class: "s" }, `range ${pct(r.range.low, 1)} to ${pct(r.range.high, 1)}`)),
        ),
      ];
      if (r.checks.length > 1) {
        blocks.push(el("h3", {}, "How much the result depends on the method"));
        blocks.push(table(["Check", "Did not hold"], r.checks.map((c) => [c.check, pct(c.didNotHold, 1)])));
        blocks.push(el("p", { class: "small muted" }, "If these differ a lot, the result depends on that choice, and the full audit should look closer."));
      }
      if (r.byDetail.length) {
        blocks.push(el("h3", {}, "By how the platform closed the conversation"));
        blocks.push(table(["Close reason", "Counted resolved", "Share", "Came back", "Did not hold"], r.byDetail.map((d) => [d.detail || "(blank)", num(d.claims), pct(d.share, 1), pct(d.cameBack, 1), pct(d.didNotHold)])));
      }
      if (r.cameBackAbout.length) {
        blocks.push(el("h3", {}, "What people came back about: where the bot is failing"));
        blocks.push(table(["Category of the ticket they opened", "Returns", "Share of returns"], r.cameBackAbout.map((c) => [c.category, num(c.returns), pct(c.share, 1)])));
      }
      if (r.byMonth.length) {
        blocks.push(el("h3", {}, "By month"));
        blocks.push(table(["Month", "Counted resolved", "Came back", "After your agents", "Did not hold"], r.byMonth.map((m) => [m.month, num(m.claims), pct(m.cameBack, 1), pct(m.afterPeople, 1), pct(m.didNotHold)])));
      }
      blocks.push(el("ul", { class: "small muted check-notes" }, ...r.notes.map((n) => el("li", {}, n))));
      const save = el("button", { class: "btn", type: "button" }, "Download the summary (JSON)");
      save.addEventListener("click", () => {
        const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
        const a = el("a", { href: URL.createObjectURL(blob), download: "resolution-check-summary.json" });
        document.body.append(a);
        a.click();
        a.remove();
      });
      const print = el("button", { class: "btn", type: "button" }, "Print or save as PDF");
      print.addEventListener("click", () => window.print());
      blocks.push(el("div", { class: "cta-row check-actions" }, print, save));
      blocks.push(el("p", { class: "small muted" }, "The summary holds rates and counts only: no names, IDs or ticket contents. It is what you would share."));
      out.replaceChildren(...blocks);
      out.hidden = false;
      out.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    for (const kind of ["conv", "tickets"]) {
      const input = $(`${kind}-file`);
      input.addEventListener("change", () => {
        if (!input.files[0]) return;
        state.sample = false;
        // Cleared once read, so choosing the same file again after editing it reloads it.
        load(kind, input.files[0])
          .catch((e) => failed(kind, e))
          .finally(() => (input.value = ""));
      });
      const zone = $(`${kind}-zone`);
      zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("over");
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("over");
        const f = e.dataTransfer.files[0];
        if (!f) return;
        state.sample = false;
        load(kind, f).catch((e) => failed(kind, e));
      });
    }
    // The example: sample exports from a simulated desk, fetched only when asked for (from this site, as a script,
    // since the page allows no other requests), then run like any other files.
    const sample = $("check-sample");
    if (sample) {
      const runSample = () => {
        const data = window.PROBATIVE_SAMPLE;
        state.sample = true;
        Promise.all([
          load("conv", new File([data.conversations], "sample-conversations.csv", { type: "text/csv" })),
          load("tickets", new File([data.tickets], "sample-incidents.csv", { type: "text/csv" })),
        ])
          .then(() => $("check-run").click())
          .catch((e) => failed("conv", e));
      };
      sample.addEventListener("click", () => {
        if (window.PROBATIVE_SAMPLE) return runSample();
        sample.disabled = true;
        const tag = document.createElement("script");
        tag.src = sample.dataset.sample;
        tag.onload = () => {
          sample.disabled = false;
          runSample();
        };
        tag.onerror = () => {
          sample.disabled = false;
          failed("conv", new Error("The sample data didn't load. Check your connection and try again."));
        };
        document.head.append(tag);
      });
      // "See an example result" on the home page lands here and runs it.
      if (window.location.hash === "#example") sample.click();
    }
    $("check-run").addEventListener("click", () => {
      const error = $("check-error");
      error.hidden = true;
      if (!ready()) return;
      try {
        const r = ProbativeCheck.run(state.conv.table, state.conv.cols, state.tickets.table, state.tickets.cols, {
          windowDays: Number($("check-window").value) || 7,
          resolvedValue: $("check-resolved").value,
          aiChannels: $("check-ai-channels").value,
          linkMinutes: $("check-link").value,
        });
        render(r);
      } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
      }
    });
  })();
}

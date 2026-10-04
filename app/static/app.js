const VIEWS = ["track", "manage", "history", "checks", "help"];
const VIEW_TITLES = {
  track: "My track",
  manage: "Management",
  history: "History",
  checks: "Admin",
  help: "Help",
};

const state = {
  view: "track",
  today: "",
  timezone: "Asia/Kolkata",
  date: "",
  tracks: [],
  trackId: null,
  submittedBy: "",
  entry: null,
  draft: {},
  board: null,
  history: null,
  historyDays: 30,
  tasks: [],
  admin: false,
  adminTracks: [],
  branding: { banner_color: "#0f5fdc", has_logo: false, logo_version: 0 },
  openTrackId: null,
  fatal: "",
};

const appEl = document.getElementById("app");

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function meaningful(text) {
  const stripped = (text || "").trim();
  const alnum = stripped.replace(/[^A-Za-z0-9]/g, "");
  return stripped.length >= 3 && alnum.length >= 3;
}

function referenceError(text) {
  const stripped = (text || "").trim();
  if (stripped.length > 40) {
    return "Service request or incident number must be 40 characters or fewer.";
  }
  if (stripped.length < 3 || !/[A-Za-z0-9]/.test(stripped)) {
    return "Amber and red need a service request or incident number.";
  }
  return "";
}

function addDays(iso, days) {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + days);
  const nextMonth = String(date.getMonth() + 1).padStart(2, "0");
  const nextDay = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${nextMonth}-${nextDay}`;
}

function formatDay(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatWhen(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function overallLabel(overall) {
  if (overall === "red") return "Red";
  if (overall === "amber") return "Amber";
  if (overall === "green") return "Green";
  return "Not filed";
}

function joinNames(names) {
  if (names.length <= 1) return names[0] || "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

function attention(tracks) {
  if (!tracks.length) return "Add a track under Checks.";
  const red = tracks.filter((track) => track.overall === "red").map((track) => track.name);
  const amber = tracks.filter((track) => track.overall === "amber").map((track) => track.name);
  const missing = tracks.filter((track) => !track.overall).map((track) => track.name);
  const parts = [];
  if (red.length) parts.push(`${joinNames(red)} ${red.length > 1 ? "are" : "is"} red`);
  if (amber.length) parts.push(`${joinNames(amber)} ${amber.length > 1 ? "are" : "is"} amber`);
  if (missing.length) parts.push(`${joinNames(missing)} ${missing.length > 1 ? "have" : "has"} not submitted`);
  if (!parts.length) return "All tracks are green.";
  return `${parts.join(". ")}.`;
}

function errorMessage(error) {
  const detail = error.payload?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg).filter(Boolean).join(" ") || "Check the form and try again.";
  }
  if (error.message === "Failed to fetch") return "Cannot reach the server.";
  return "Something went wrong. Try again.";
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  opts.headers = {
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  let response;
  try {
    response = await fetch(path, opts);
  } catch (error) {
    error.payload = null;
    throw error;
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const error = new Error("Request failed");
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

function ensureTrack() {
  const stored = Number(localStorage.getItem("health.trackId"));
  if (state.tracks.some((track) => track.id === stored)) {
    state.trackId = stored;
    return;
  }
  state.trackId = state.tracks[0]?.id ?? null;
}

function setBanner(kind, text) {
  const banner = document.getElementById("banner");
  if (!banner) return;
  banner.hidden = !text;
  banner.className = `banner ${kind || ""}`.trim();
  banner.textContent = text || "";
}

function showFieldErrors(fields) {
  document.querySelectorAll(".task-card, .name-field").forEach((node) => node.classList.remove("invalid"));
  document.querySelectorAll(".field-error").forEach((node) => node.remove());
  for (const field of fields || []) {
    if (field.field === "submitted_by") {
      const wrap = document.querySelector(".name-field");
      if (!wrap) continue;
      wrap.classList.add("invalid");
      const note = document.createElement("p");
      note.className = "field-error";
      note.textContent = field.message;
      wrap.append(note);
      continue;
    }
    const card = document.querySelector(`.task-card[data-task-id="${field.task_id}"]`);
    if (!card) continue;
    card.classList.add("invalid");
    const note = document.createElement("p");
    note.className = "field-error";
    note.textContent = field.message;
    card.append(note);
  }
  document.querySelector(".invalid")?.scrollIntoView({ block: "center" });
}

async function loadEntry() {
  const entry = await api(`/api/entry?track_id=${state.trackId}&date=${state.date}`);
  state.entry = entry;
  state.draft = {};
  const prior = new Map((entry.submission?.items || []).map((item) => [item.task_id, item]));
  for (const task of entry.tasks) {
    const saved = prior.get(task.id);
    state.draft[task.id] = {
      status: saved?.status || "",
      comment: saved?.comment || "",
      reference: saved?.reference || "",
    };
  }
  state.submittedBy = entry.submission?.submitted_by || localStorage.getItem("health.submittedBy") || "";
}

async function loadBoard() {
  state.board = await api(`/api/board?date=${state.date}`);
}

async function loadHistory() {
  state.history = await api(`/api/history?days=${state.historyDays}&end=${state.date}`);
}

async function loadTasks() {
  state.tasks = await api(`/api/tasks?track_id=${state.trackId}&include_inactive=true`);
}

async function refresh() {
  state.fatal = "";
  try {
    if (state.view === "help") {
      render();
      return;
    }
    if (!state.trackId && state.view !== "checks") {
      render();
      return;
    }
    if (state.view === "track") await loadEntry();
    else if (state.view === "manage") await loadBoard();
    else if (state.view === "history") await loadHistory();
    else if (!state.admin) {
      render();
      return;
    } else {
      state.adminTracks = await api("/api/tracks?include_inactive=true");
      if (!state.adminTracks.some((track) => track.id === state.trackId)) {
        const active = state.adminTracks.find((track) => track.active);
        state.trackId = active?.id ?? state.adminTracks[0]?.id ?? null;
      }
      if (state.trackId) await loadTasks();
      else state.tasks = [];
    }
    render();
  } catch (error) {
    state.fatal = errorMessage(error);
    render();
  }
}

function navButton(view, label) {
  const current = state.view === view ? ' aria-current="page"' : "";
  return `<button type="button" class="nav-btn" data-nav="${view}"${current}>${label}</button>`;
}

function dateBar() {
  const nextDisabled = state.date >= state.today ? "disabled" : "";
  return `
    <div class="date-bar">
      <button type="button" class="icon-btn" data-action="shift" data-days="-1" aria-label="Previous day">←</button>
      <input id="check-date" type="date" max="${esc(state.today)}" value="${esc(state.date)}" aria-label="Check date">
      <button type="button" class="icon-btn" data-action="shift" data-days="1" ${nextDisabled} aria-label="Next day">→</button>
      <button type="button" class="text-btn" data-action="today">Today</button>
    </div>
  `;
}

function inkFor(hex) {
  const red = Number.parseInt(hex.slice(1, 3), 16);
  const green = Number.parseInt(hex.slice(3, 5), 16);
  const blue = Number.parseInt(hex.slice(5, 7), 16);
  const luminance = (red * 299 + green * 587 + blue * 114) / 1000;
  return luminance > 160 ? "#1c1915" : "#f6f3ec";
}

function applyBranding() {
  const color = state.branding?.banner_color || "#0f5fdc";
  document.documentElement.style.setProperty("--banner", color);
  document.documentElement.style.setProperty("--banner-ink", inkFor(color));
}

function brandMark() {
  if (!state.branding?.has_logo) {
    return `<span class="mark" aria-hidden="true"><i></i><i></i><i></i></span>`;
  }
  const version = state.branding.logo_version || 0;
  return `<img class="logo" src="/api/branding/logo?v=${version}" alt="Company logo">`;
}

function shell(content) {
  applyBranding();
  return `
    <a class="skip" href="#content">Skip to content</a>
    <header class="topbar">
      <div class="brand">
        ${brandMark()}
        <div>
          <p class="brand-kicker">Operations</p>
          <h1>Daily Health</h1>
        </div>
      </div>
      <nav class="nav" aria-label="Sections">
        ${navButton("track", "My track")}
        ${navButton("manage", "Management")}
        ${navButton("history", "History")}
        ${navButton("checks", "Admin")}
        ${navButton("help", "Help")}
      </nav>
    </header>
    <main id="content" class="wrap">
      <div id="banner" class="banner" hidden></div>
      ${content}
      <div class="foot">
        <p>A track is red when any check is red, and amber when any remaining check is amber. Comments and a service request or incident number are required for red and amber. The day boundary follows ${esc(state.timezone)}.</p>
        <p class="copyright">© 2026 HCLTech</p>
      </div>
    </main>
  `;
}

function trackPicker(tracks = state.tracks) {
  return `
    <div class="track-picker" role="group" aria-label="Tracks">
      ${tracks.map((track) => `
        <button type="button" class="track-pick ${track.active === false ? "inactive" : ""}" data-action="pick-track" data-track-id="${track.id}" aria-pressed="${track.id === state.trackId}">
          ${esc(track.name)}${track.active === false ? " (removed)" : ""}
        </button>
      `).join("")}
    </div>
  `;
}

function renderTrack() {
  if (!state.trackId) {
    return `<section class="panel"><h2>No tracks yet</h2><p class="lede">Add one under Checks.</p></section>`;
  }
  const entry = state.entry;
  const locked = entry.editable === false;
  const submission = entry.submission;
  const saved = submission
    ? `Last saved by ${submission.submitted_by} at ${formatWhen(submission.updated_at)}.`
    : "Nothing filed for this day yet.";
  const tasks = entry.tasks.map((task) => {
    const draft = state.draft[task.id];
    const required = draft.status === "red" || draft.status === "amber";
    return `
      <article class="task-card ${locked ? "locked" : ""}" data-task-id="${task.id}" data-status="${esc(draft.status)}">
        <h3>${esc(task.title)}</h3>
        <p class="guidance">${esc(task.guidance)}</p>
        <div class="rag" role="radiogroup" aria-label="${esc(task.title)} status">
          ${["green", "amber", "red"].map((value) => `
            <label class="rag-opt ${value} ${draft.status === value ? "is-selected" : ""}">
              <input type="radio" name="status-${task.id}" value="${value}" ${draft.status === value ? "checked" : ""} ${locked ? "disabled" : ""}>
              <span>${value[0].toUpperCase()}${value.slice(1)}</span>
            </label>
          `).join("")}
        </div>
        <label class="comment-field">
          <span class="comment-label">${required ? "Comment (required)" : "Note (optional)"}</span>
          <textarea rows="3" aria-required="${required}" ${locked ? "disabled" : ""} placeholder="${esc(required ? "What is wrong, who is affected, and what happens next?" : "Optional note")}">${esc(draft.comment)}</textarea>
        </label>
        <label class="comment-field reference-field"${required ? "" : " hidden"}>
          <span class="reference-label">Service request or incident number (required)</span>
          <input class="reference-input" type="text" maxlength="40" aria-required="${required}" ${locked ? "disabled" : ""} placeholder="INC0012345, SR123456, or RITM0012345" value="${esc(draft.reference)}">
        </label>
      </article>
    `;
  }).join("");
  const body = entry.tasks.length
    ? `<div class="task-list">${tasks}</div>`
    : `<p>No checks are defined for this track yet. An admin can add them.</p>`;
  const done = entry.tasks.filter((task) => state.draft[task.id]?.status).length;
  return `
    <div class="page-head">
      <div>
        <h2>My track</h2>
        <p class="lede">${esc(formatDay(state.date))}. ${locked ? "This day is closed and cannot be changed." : "Choose the track, mark every check, and save. Earlier days can be read, but only today can be filed or updated."}</p>
      </div>
      ${dateBar()}
    </div>
    ${trackPicker()}
    <section class="panel">
      <div class="identity">
        <label class="name-field">
          <span>Your name</span>
          <input id="submitted-by" type="text" maxlength="80" autocomplete="name" value="${esc(state.submittedBy)}" placeholder="Who is filing this check" ${locked ? "disabled" : ""}>
        </label>
        <p class="saved-line">${esc(saved)}${submission ? ` Overall: ${overallLabel(submission.overall)}.` : ""}</p>
      </div>
      ${body}
      ${locked
        ? `<p class="locked-note">Past days are locked. Only today can be filed or updated.</p>`
        : `<div class="savebar">
        <p id="progress">${done} of ${entry.tasks.length} checks marked</p>
        <button type="button" class="primary" id="save-entry" data-action="save-entry" ${entry.tasks.length ? "" : "disabled"}>Save this day</button>
      </div>`}
    </section>
  `;
}

function leadIssue(submission) {
  if (!submission) return "No submission for this day.";
  const rank = { red: 0, amber: 1, green: 2 };
  const issues = submission.items
    .filter((item) => item.status === "red" || item.status === "amber")
    .sort((a, b) => rank[a.status] - rank[b.status]);
  if (!issues.length) return "All checks are green.";
  const first = issues[0];
  const extra = issues.length - 1;
  const line = `${first.task_title}: ${first.comment}`;
  return extra > 0 ? `${line} (+${extra} more)` : line;
}

function renderManage() {
  const board = state.board;
  const cards = board.tracks.map((track) => {
    const status = track.overall || "missing";
    const open = state.openTrackId === track.id;
    const who = track.submission
      ? `${track.submission.submitted_by} · ${formatWhen(track.submission.updated_at)}`
      : "Waiting for the track";
    const counts = track.submission
      ? `${track.counts.red} red · ${track.counts.amber} amber · ${track.counts.green} green`
      : "Not submitted";
    const items = (track.submission?.items || [])
      .slice()
      .sort((a, b) => ({ red: 0, amber: 1, green: 2 })[a.status] - ({ red: 0, amber: 1, green: 2 })[b.status])
      .map((item) => `
        <div class="item">
          <span class="pill ${item.status}">${overallLabel(item.status)}</span>
          <div>
            <strong>${esc(item.task_title)}</strong>
            ${item.comment || item.reference ? `
              <p class="item-note">
                ${item.comment ? `<span>${esc(item.comment)}</span>` : ""}
                ${item.reference ? `<span class="item-ref">${esc(item.reference)}</span>` : ""}
              </p>
            ` : ""}
          </div>
        </div>
      `).join("");
    const detail = track.submission
      ? items
      : `<p class="card-issue">This track has not filed ${esc(formatDay(board.date))}.</p>`;
    return `
      <article class="track-card status-${status} ${open ? "is-open" : ""}" data-track-id="${track.id}">
        <button type="button" class="card-open" data-action="toggle-track" data-track-id="${track.id}" aria-expanded="${open}">
          <span class="card-kicker">Track</span>
          <span class="card-name">${esc(track.name)}</span>
          <span class="card-overall">${overallLabel(track.overall)}</span>
          <span class="card-counts">${esc(counts)}</span>
          <span class="card-who">${esc(who)}</span>
          <span class="card-issue">${esc(leadIssue(track.submission))}</span>
        </button>
        <div class="card-detail">${detail}</div>
      </article>
    `;
  }).join("");
  return `
    <div class="page-head">
      <div>
        <h2>Management</h2>
        <p class="lede">Track-wise rollup for ${esc(formatDay(board.date))}. Open a track to read the checks behind the colour.</p>
      </div>
      ${dateBar()}
    </div>
    <div class="stats">
      <div class="stat red"><strong>${board.summary.red}</strong><span>Red</span></div>
      <div class="stat amber"><strong>${board.summary.amber}</strong><span>Amber</span></div>
      <div class="stat green"><strong>${board.summary.green}</strong><span>Green</span></div>
      <div class="stat"><strong>${board.summary.missing}</strong><span>Not filed</span></div>
    </div>
    <p class="attention">${esc(attention(board.tracks))}</p>
    <div class="cards">${cards}</div>
  `;
}

function renderHistory() {
  const history = state.history;
  const head = history.dates.map((day) => {
    const [, month, date] = day.split("-");
    const monthName = new Date(Number(day.slice(0, 4)), Number(month) - 1, 1)
      .toLocaleDateString(undefined, { month: "short" });
    return `<th title="${esc(formatDay(day))}"><span class="day-num">${Number(date)}</span><span class="day-mon">${esc(monthName)}</span></th>`;
  }).join("");
  const rows = history.tracks.map((track) => {
    const cells = track.days.map((day) => {
      const status = day.overall || "missing";
      const label = `${track.name}, ${formatDay(day.date)}, ${overallLabel(day.overall)}`;
      return `<td><button type="button" class="cell ${status}" data-action="open-day" data-date="${day.date}" data-track-id="${track.id}" title="${esc(label)}" aria-label="${esc(label)}"></button></td>`;
    }).join("");
    return `<tr><th class="track-col">${esc(track.name)}</th>${cells}</tr>`;
  }).join("");
  return `
    <div class="page-head">
      <div>
        <h2>History</h2>
        <p class="lede">Each cell is that track's overall colour. Choose a cell to open the day on the management view.</p>
      </div>
      <div>
        ${dateBar()}
        <div class="range" style="margin-top:8px">
          <button type="button" class="text-btn" data-action="history-days" data-days="14" aria-pressed="${state.historyDays === 14}">14 days</button>
          <button type="button" class="text-btn" data-action="history-days" data-days="30" aria-pressed="${state.historyDays === 30}">30 days</button>
        </div>
      </div>
    </div>
    <div class="matrix-wrap">
      <table class="matrix">
        <thead><tr><th class="track-col">Track</th>${head}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="legend">
      <span><i class="green"></i>Green</span>
      <span><i class="amber"></i>Amber</span>
      <span><i class="red"></i>Red</span>
      <span><i class="missing"></i>Not filed</span>
    </div>
  `;
}

function renderHelp() {
  return `
    <div class="page-head">
      <div>
        <h2>How to use Daily Health</h2>
        <p class="lede">This site is for an internal network. It is where each operations track files a daily health check, and where others read the result.</p>
      </div>
    </div>
    <section class="panel help-page">
      <div class="help-block">
        <h3>Who uses each view</h3>
        <ul>
          <li><strong>My track</strong> is for the person who files that track's checks for the day.</li>
          <li><strong>Management</strong> is for people who need the day's colour for every track, and the comment behind a red or amber check.</li>
          <li><strong>History</strong> is for anyone looking back across earlier days.</li>
          <li><strong>Admin</strong> is for the person who maintains tracks, checks, the logo, and the banner colour. Sign-in is required. The default username is admin.</li>
        </ul>
      </div>
      <div class="help-block">
        <h3>Filing a daily check</h3>
        <p>Open My track, choose the day and your track, and enter your name. Mark every check green, amber, or red, then save.</p>
        <p>The track colour follows the checks: red if any check is red, amber if any remaining check is amber, otherwise green.</p>
      </div>
      <div class="help-block">
        <h3>Red and amber</h3>
        <p>Red and amber need both a comment and a service request or incident number, such as INC0012345, SR123456, or RITM0012345. Green can be saved with the number left blank.</p>
      </div>
      <div class="help-block">
        <h3>Past days</h3>
        <p>Past days can be opened from My track, Management, or History, but they cannot be changed. Only today can be filed or updated.</p>
      </div>
      <div class="help-block">
        <h3>Admin</h3>
        <p>Admin sign-in is required to add or remove tracks and checks, and to set the logo and banner colour. Filing a daily check does not need that login.</p>
      </div>
    </section>
  `;
}

function renderLogin() {
  return `
    <div class="page-head">
      <div>
        <h2>Admin</h2>
        <p class="lede">Sign in to add or remove tracks and checks. Daily status filing stays open without this login.</p>
      </div>
    </div>
    <section class="panel login-panel">
      <form data-form="login" class="login-form">
        <label>
          <span>Username</span>
          <input name="username" type="text" autocomplete="username" required>
        </label>
        <label>
          <span>Password</span>
          <input name="password" type="password" autocomplete="current-password" required>
        </label>
        <button type="submit" class="primary">Sign in</button>
      </form>
    </section>
  `;
}

function renderChecks() {
  if (!state.admin) return renderLogin();
  const tracks = state.adminTracks.length ? state.adminTracks : state.tracks;
  const current = tracks.find((track) => track.id === state.trackId);
  const rows = state.tasks.map((task) => `
    <div class="check-row ${task.active ? "" : "inactive"}" data-task-id="${task.id}">
      <input class="task-title" type="text" value="${esc(task.title)}" aria-label="Check title" maxlength="120" ${task.active ? "" : "disabled"}>
      <textarea class="task-guidance" rows="2" aria-label="Guidance" maxlength="400" ${task.active ? "" : "disabled"}>${esc(task.guidance)}</textarea>
      <div class="row-actions">
        ${task.active ? `
          <button type="button" class="text-btn" data-action="move-task" data-move="up">Up</button>
          <button type="button" class="text-btn" data-action="move-task" data-move="down">Down</button>
          <button type="button" class="text-btn" data-action="save-task">Save</button>
          <button type="button" class="text-btn danger" data-action="remove-task">Remove</button>
        ` : `
          <button type="button" class="text-btn" data-action="restore-task">Restore</button>
        `}
      </div>
    </div>
  `).join("");
  return `
    <div class="page-head">
      <div>
        <h2>Admin</h2>
        <p class="lede">Add or remove tracks and the checks each track answers. A removed item that was already filed stays in history.</p>
      </div>
      <button type="button" class="text-btn" data-action="logout">Sign out</button>
    </div>
    <section class="panel" style="margin-bottom:16px">
      <h3>Appearance</h3>
      <form data-form="branding" class="brand-form">
        <label>
          <span>Banner colour</span>
          <input id="banner-color" type="color" value="${esc(state.branding.banner_color)}" aria-label="Banner colour">
        </label>
        <label>
          <span>Company logo</span>
          <input id="logo-file" type="file" accept="image/png,image/jpeg,image/webp,image/gif" aria-label="Company logo">
        </label>
        ${state.branding.has_logo ? `<img class="logo-preview" src="/api/branding/logo?v=${state.branding.logo_version || 0}" alt="Current company logo">` : `<p class="lede">PNG, JPEG, WEBP, or GIF. Up to 512 KB.</p>`}
        <div class="inline-actions">
          <button type="submit" class="primary">Save appearance</button>
          ${state.branding.has_logo ? `<button type="button" class="text-btn danger" data-action="remove-logo">Remove logo</button>` : ""}
        </div>
      </form>
    </section>
    ${trackPicker(tracks)}
    <div class="split">
      <section class="panel">
        <h3>Track name</h3>
        <form data-form="rename-track" class="inline-actions" style="margin-top:12px">
          <input id="track-name" type="text" value="${esc(current?.name || "")}" maxlength="120" aria-label="Track name" ${current ? "" : "disabled"}>
          <button type="submit" class="primary" ${current ? "" : "disabled"}>Rename</button>
          ${current?.active === false
            ? `<button type="button" class="text-btn" data-action="restore-track">Restore track</button>`
            : `<button type="button" class="text-btn danger" data-action="remove-track" ${current ? "" : "disabled"}>Remove track</button>`}
        </form>
      </section>
      <section class="panel">
        <h3>Add a track</h3>
        <form data-form="add-track" class="inline-actions" style="margin-top:12px">
          <input id="new-track-name" type="text" maxlength="120" placeholder="Network" aria-label="New track name">
          <button type="submit" class="primary">Add track</button>
        </form>
      </section>
    </div>
    <section class="panel" style="margin-top:16px">
      <h3>${esc(current?.name || "Track")} checks</h3>
      ${current?.active === false ? `<p class="lede">This track is removed. Restore it before adding checks.</p>` : `
        <form data-form="add-task" class="check-row">
          <input id="new-task-title" type="text" maxlength="120" placeholder="New check" aria-label="New check title">
          <textarea id="new-task-guidance" rows="2" maxlength="400" placeholder="What good looks like" aria-label="New check guidance"></textarea>
          <button type="submit" class="primary">Add check</button>
        </form>
      `}
      ${rows || `<p class="lede">No checks yet.</p>`}
    </section>
  `;
}

function render() {
  document.title = `${VIEW_TITLES[state.view] || "Daily Health"} · Daily Health`;
  if (state.fatal) {
    appEl.innerHTML = shell(`
      <section class="panel">
        <h2>This view did not load</h2>
        <p class="lede">${esc(state.fatal)}</p>
        <button type="button" class="primary" data-action="retry" style="margin-top:12px">Try again</button>
      </section>
    `);
    return;
  }
  const views = {
    track: renderTrack,
    manage: renderManage,
    history: renderHistory,
    checks: renderChecks,
    help: renderHelp,
  };
  appEl.innerHTML = shell(views[state.view]());
}

function updateProgress() {
  const tasks = state.entry?.tasks || [];
  const done = tasks.filter((task) => state.draft[task.id]?.status).length;
  const progress = document.getElementById("progress");
  if (progress) progress.textContent = `${done} of ${tasks.length} checks marked`;
}

function syncCard(card) {
  const selected = card.querySelector("input[type=radio]:checked");
  const status = selected ? selected.value : "";
  state.draft[card.dataset.taskId].status = status;
  card.dataset.status = status;
  card.classList.remove("invalid");
  card.querySelector(".field-error")?.remove();
  card.querySelectorAll(".rag-opt").forEach((option) => {
    option.classList.toggle("is-selected", option.querySelector("input").checked);
  });
  const required = status === "red" || status === "amber";
  card.querySelector(".comment-label").textContent = required ? "Comment (required)" : "Note (optional)";
  const area = card.querySelector("textarea");
  area.setAttribute("aria-required", required ? "true" : "false");
  area.placeholder = required
    ? "What is wrong, who is affected, and what happens next?"
    : "Optional note";
  const referenceField = card.querySelector(".reference-field");
  referenceField.hidden = !required;
  referenceField.querySelector("input").setAttribute("aria-required", required ? "true" : "false");
  updateProgress();
}

function validateDraft() {
  const fields = [];
  if (state.submittedBy.trim().length < 2) {
    fields.push({ field: "submitted_by", message: "Enter your name." });
  }
  for (const task of state.entry.tasks) {
    const item = state.draft[task.id];
    if (!item?.status) {
      fields.push({ task_id: task.id, field: "status", message: "Choose green, amber, or red." });
    } else if ((item.status === "red" || item.status === "amber") && !meaningful(item.comment)) {
      fields.push({
        task_id: task.id,
        field: "comment",
        message: "Amber and red need a comment that describes the issue.",
      });
    } else if (item.status === "red" || item.status === "amber") {
      const message = referenceError(item.reference);
      if (message) fields.push({ task_id: task.id, field: "reference", message });
    }
  }
  return fields;
}

async function saveEntry() {
  if (state.entry?.editable === false || state.date < state.today) {
    setBanner("error", "A past day cannot be changed.");
    return;
  }
  const button = document.getElementById("save-entry");
  const fields = validateDraft();
  if (fields.length) {
    showFieldErrors(fields);
    setBanner("error", "Some checks still need attention.");
    return;
  }
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api("/api/submissions", {
      method: "POST",
      body: JSON.stringify({
        track_id: state.trackId,
        check_date: state.date,
        submitted_by: state.submittedBy.trim(),
        items: state.entry.tasks.map((task) => {
          const draft = state.draft[task.id];
          const attention = draft.status === "red" || draft.status === "amber";
          return {
            task_id: task.id,
            status: draft.status,
            comment: draft.comment,
            reference: attention ? draft.reference.trim() : "",
          };
        }),
      }),
    });
    localStorage.setItem("health.submittedBy", state.submittedBy.trim());
    await loadEntry();
    render();
    setBanner("ok", `Saved ${state.entry.track.name} for ${formatDay(state.date)}.`);
  } catch (error) {
    const fieldsFromServer = error.payload?.detail?.fields;
    if (Array.isArray(fieldsFromServer) && fieldsFromServer.length) showFieldErrors(fieldsFromServer);
    if (error.status === 422 && /checklist changed/i.test(errorMessage(error))) {
      await loadEntry();
      render();
    } else if (button) {
      button.disabled = false;
      button.textContent = "Save this day";
    }
    setBanner("error", errorMessage(error));
  }
}

async function pickTrack(id) {
  state.trackId = id;
  localStorage.setItem("health.trackId", String(id));
  if (state.view === "track") await loadEntry();
  if (state.view === "checks" && state.admin) await loadTasks();
  render();
}

async function reloadTrackLists() {
  state.tracks = await api("/api/tracks");
  if (state.admin) state.adminTracks = await api("/api/tracks?include_inactive=true");
  const pool = state.admin && state.view === "checks" ? state.adminTracks : state.tracks;
  if (!pool.some((track) => track.id === state.trackId)) {
    state.trackId = pool.find((track) => track.active !== false)?.id ?? pool[0]?.id ?? null;
    localStorage.setItem("health.trackId", String(state.trackId ?? ""));
  }
}

async function shiftDate(days) {
  const next = addDays(state.date, days);
  if (next > state.today) return;
  state.date = next;
  await refresh();
}

async function goToday() {
  const meta = await api("/api/meta");
  state.today = meta.today;
  state.timezone = meta.timezone;
  state.date = meta.today;
  await refresh();
}

async function addTrack(form) {
  const name = form.querySelector("input").value.trim();
  try {
    const track = await api("/api/tracks", { method: "POST", body: JSON.stringify({ name }) });
    state.trackId = track.id;
    await reloadTrackLists();
    localStorage.setItem("health.trackId", String(track.id));
    await loadTasks();
    render();
    setBanner("ok", `${track.name} added.`);
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function renameTrack(form) {
  const name = form.querySelector("input").value.trim();
  try {
    const track = await api(`/api/tracks/${state.trackId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    await reloadTrackLists();
    render();
    setBanner("ok", `Track renamed to ${track.name}.`);
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function addTask(form) {
  const title = form.querySelector("#new-task-title").value.trim();
  const guidance = form.querySelector("#new-task-guidance").value.trim();
  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ track_id: state.trackId, title, guidance }),
    });
    await loadTasks();
    render();
    setBanner("ok", "Check added.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function saveTask(row) {
  const title = row.querySelector(".task-title").value.trim();
  const guidance = row.querySelector(".task-guidance").value.trim();
  try {
    await api(`/api/tasks/${row.dataset.taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ title, guidance }),
    });
    await loadTasks();
    render();
    setBanner("ok", "Check saved.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function moveTask(row, direction) {
  try {
    await api(`/api/tasks/${row.dataset.taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ move: direction }),
    });
    await loadTasks();
    render();
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function login(form) {
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        username: form.querySelector("[name=username]").value,
        password: form.querySelector("[name=password]").value,
      }),
    });
    state.admin = true;
    await refresh();
    setBanner("ok", "Signed in.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

function readLogoFile(file) {
  return new Promise((resolve, reject) => {
    if (!file) {
      resolve(null);
      return;
    }
    if (file.size > 512000) {
      reject(new Error("Logo must be 512 KB or smaller."));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Could not read that logo."));
    reader.readAsDataURL(file);
  });
}

async function saveBranding(form) {
  try {
    const logo = await readLogoFile(form.querySelector("#logo-file").files[0]);
    const saved = await api("/api/branding", {
      method: "PUT",
      body: JSON.stringify({
        banner_color: form.querySelector("#banner-color").value,
        ...(logo ? { logo_data_url: logo } : {}),
      }),
    });
    state.branding = { ...saved, logo_version: Date.now() };
    render();
    setBanner("ok", "Appearance saved.");
  } catch (error) {
    setBanner("error", error.payload ? errorMessage(error) : error.message);
  }
}

async function removeLogo() {
  try {
    const saved = await api("/api/branding", {
      method: "PUT",
      body: JSON.stringify({
        banner_color: state.branding.banner_color,
        remove_logo: true,
      }),
    });
    state.branding = { ...saved, logo_version: Date.now() };
    render();
    setBanner("ok", "Logo removed.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST" });
  } catch (error) {
    setBanner("error", errorMessage(error));
    return;
  }
  state.admin = false;
  state.adminTracks = [];
  state.tasks = [];
  render();
}

async function removeTrack() {
  if (!state.trackId) return;
  try {
    const result = await api(`/api/tracks/${state.trackId}`, { method: "DELETE" });
    await reloadTrackLists();
    if (state.trackId) await loadTasks();
    else state.tasks = [];
    render();
    setBanner("ok", result.kept_history
      ? "Track removed from the daily form. Days already filed stay in history."
      : "Track removed.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function restoreTrack() {
  try {
    await api(`/api/tracks/${state.trackId}`, {
      method: "PATCH",
      body: JSON.stringify({ active: true }),
    });
    await reloadTrackLists();
    await loadTasks();
    render();
    setBanner("ok", "Track restored.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function removeTask(row) {
  try {
    const result = await api(`/api/tasks/${row.dataset.taskId}`, { method: "DELETE" });
    await loadTasks();
    render();
    setBanner("ok", result.kept_history
      ? "Check removed from new updates. Past days still show it."
      : "Check removed.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

async function restoreTask(row) {
  try {
    await api(`/api/tasks/${row.dataset.taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ active: true }),
    });
    await loadTasks();
    render();
    setBanner("ok", "Check restored.");
  } catch (error) {
    setBanner("error", errorMessage(error));
  }
}

function onClick(event) {
  const nav = event.target.closest("[data-nav]");
  if (nav) {
    if (location.hash !== `#${nav.dataset.nav}`) location.hash = nav.dataset.nav;
    return;
  }
  const action = event.target.closest("[data-action]");
  if (!action) return;
  const name = action.dataset.action;
  if (name === "retry") refresh();
  if (name === "today") goToday();
  if (name === "shift") shiftDate(Number(action.dataset.days));
  if (name === "pick-track") pickTrack(Number(action.dataset.trackId));
  if (name === "save-entry") saveEntry();
  if (name === "toggle-track") {
    const id = Number(action.dataset.trackId);
    state.openTrackId = state.openTrackId === id ? null : id;
    render();
  }
  if (name === "open-day") {
    state.date = action.dataset.date;
    state.openTrackId = Number(action.dataset.trackId);
    location.hash = "manage";
  }
  if (name === "history-days") {
    state.historyDays = Number(action.dataset.days);
    refresh();
  }
  if (name === "save-task") saveTask(action.closest(".check-row"));
  if (name === "move-task") moveTask(action.closest(".check-row"), action.dataset.move);
  if (name === "remove-task") removeTask(action.closest(".check-row"));
  if (name === "restore-task") restoreTask(action.closest(".check-row"));
  if (name === "remove-track") removeTrack();
  if (name === "restore-track") restoreTrack();
  if (name === "logout") logout();
  if (name === "remove-logo") removeLogo();
}

function onInput(event) {
  if (event.target.id === "banner-color") {
    document.documentElement.style.setProperty("--banner", event.target.value);
    document.documentElement.style.setProperty("--banner-ink", inkFor(event.target.value));
    return;
  }
  if (event.target.id === "submitted-by") {
    state.submittedBy = event.target.value;
    document.querySelector(".name-field")?.classList.remove("invalid");
    return;
  }
  const card = event.target.closest(".task-card");
  if (!card || card.classList.contains("locked")) return;
  if (event.target.matches("textarea")) {
    state.draft[card.dataset.taskId].comment = event.target.value;
  } else if (event.target.matches(".reference-input")) {
    state.draft[card.dataset.taskId].reference = event.target.value;
  } else {
    return;
  }
  card.classList.remove("invalid");
  card.querySelector(".field-error")?.remove();
}

function onChange(event) {
  if (event.target.id === "check-date") {
    const next = event.target.value;
    if (!next || next > state.today) {
      event.target.value = state.date;
      return;
    }
    state.date = next;
    refresh();
    return;
  }
  const card = event.target.closest(".task-card");
  if (card?.classList.contains("locked")) return;
  if (card && event.target.matches("input[type=radio]")) syncCard(card);
}

function onSubmit(event) {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.form === "add-track") addTrack(form);
  if (form.dataset.form === "rename-track") renameTrack(form);
  if (form.dataset.form === "add-task") addTask(form);
  if (form.dataset.form === "login") login(form);
  if (form.dataset.form === "branding") saveBranding(form);
}

window.addEventListener("hashchange", () => {
  const hash = location.hash.replace("#", "");
  if (!VIEWS.includes(hash) || hash === state.view) return;
  state.view = hash;
  refresh();
});

appEl.addEventListener("click", onClick);
appEl.addEventListener("input", onInput);
appEl.addEventListener("change", onChange);
appEl.addEventListener("submit", onSubmit);

async function init() {
  try {
    const meta = await api("/api/meta");
    state.branding = { ...(await api("/api/branding")), logo_version: Date.now() };
    state.admin = Boolean((await api("/api/session")).admin);
    state.today = meta.today;
    state.timezone = meta.timezone;
    state.date = meta.today;
    state.tracks = await api("/api/tracks");
    ensureTrack();
    const hash = location.hash.replace("#", "");
    state.view = VIEWS.includes(hash) ? hash : "track";
    if (location.hash !== `#${state.view}`) history.replaceState(null, "", `#${state.view}`);
    await refresh();
  } catch (error) {
    state.fatal = errorMessage(error);
    render();
  }
}

init();

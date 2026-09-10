/* NFL Predictor — single-page frontend. No build step, no framework.
   Talks to the FastAPI backend under /api and renders via hash routes. */
(() => {
  "use strict";

  // ---------------------------------------------------------------- utils
  const $ = (sel, el = document) => el.querySelector(sel);
  const view = $("#view");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (p) => `${Math.round(p * 100)}%`;
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const fmtDay = (isoDate) => new Date(isoDate + "T12:00:00").toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
  const fmtDayShort = (isoDate) => new Date(isoDate + "T12:00:00").toLocaleDateString([], { month: "short", day: "numeric" });
  const sign = (n, d = 0) => (n > 0 ? "+" : "") + Number(n).toFixed(d);

  let meta = null;
  let toastTimer = null;
  function toast(msg, isErr = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 3200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try { const j = await res.json(); msg = j.detail || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    return res.json();
  }

  function logo(team, cls = "logo") {
    // Falls back to a coloured badge if ESPN's CDN is unreachable.
    return `<img class="${cls}" src="${esc(team.logo_url)}" alt="" loading="lazy"
              onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'badge ${cls === "logo" ? "" : cls}',textContent:'${esc(team.abbr)}',style:'background:#${esc(team.color)}'}))">`;
  }

  function colorDistance(a, b) {
    const rgb = (hex) => [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) || 0);
    const [r1, g1, b1] = rgb(a), [r2, g2, b2] = rgb(b);
    return Math.hypot(r1 - r2, g1 - g2, b1 - b2);
  }
  function barColors(away, home) {
    // Two navy teams (e.g. NE at SEA) would render as one bar; fall back to the
    // away team's alternate colour when the primaries are too close.
    let a = away.color, h = home.color;
    if (colorDistance(a, h) < 60) a = away.alt_color && colorDistance(away.alt_color, h) >= 60 ? away.alt_color : "9ca3af";
    return [a, h];
  }

  function probBar(away, home, homeProb, big = false) {
    const h = Math.round(homeProb * 100), a = 100 - h;
    const [awayColor, homeColor] = barColors(away, home);
    return `<div class="prob${big ? " big" : ""}">
      <div class="labels"><span>${esc(away.abbr)} ${a}%</span><span>${esc(home.abbr)} ${h}%</span></div>
      <div class="bar"><div style="width:${a}%;background:#${esc(awayColor)}"></div><div style="width:${h}%;background:#${esc(homeColor)}"></div></div>
    </div>`;
  }

  function errorBox(err) {
    return `<div class="error"><strong>Something went wrong.</strong><div class="muted small">${esc(err.message || err)}</div></div>`;
  }

  // ---------------------------------------------------------------- router
  const routes = {
    week: renderWeek, game: renderGame, teams: renderTeams, team: renderTeam,
    picks: renderPicks, leaderboard: renderLeaderboard, how: renderHow,
  };

  async function route() {
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    const name = parts[0] || "week";
    const arg = parts[1];
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (name === "game" ? "week" : name === "team" ? "teams" : name)));
    const fn = routes[name] || renderWeek;
    view.innerHTML = `<div class="loading">Loading…</div>`;
    try {
      if (!meta) meta = await api("/api/meta");
      await fn(arg);
    } catch (err) {
      view.innerHTML = errorBox(err);
    }
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- week view
  async function renderWeek(weekArg) {
    const week = Number(weekArg) || meta.current_week;
    const data = await api(`/api/weeks/${week}`);
    const byDay = new Map();
    for (const g of data.games) {
      const d = g.game.gameday;
      if (!byDay.has(d)) byDay.set(d, []);
      byDay.get(d).push(g);
    }
    const weeksHtml = meta.weeks.map((w) =>
      `<a href="#/week/${w}" class="${w === week ? "active" : ""} ${w === meta.current_week ? "current" : ""}">${w}</a>`).join("");
    const idx = meta.weeks.indexOf(week);
    const prev = meta.weeks[idx - 1], next = meta.weeks[idx + 1];

    view.innerHTML = `
      <div class="row spread" style="margin-bottom:.5rem">
        <div><h1>Week ${week} <span class="muted" style="font-weight:500;font-size:1rem">· ${data.season} season</span></h1>
          <div class="muted small">Model picks and predicted scores for ${data.games.length} games. Tap a team to make your pick; tap a card for the breakdown and what-if tools.</div></div>
      </div>
      <div class="weekbar">
        <a class="btn btn-sm" href="#/week/${prev ?? week}" ${prev ? "" : 'style="visibility:hidden"'}>‹</a>
        <div class="weeks">${weeksHtml}</div>
        <a class="btn btn-sm" href="#/week/${next ?? week}" ${next ? "" : 'style="visibility:hidden"'}>›</a>
      </div>
      ${[...byDay.entries()].map(([day, games]) => `
        <div class="dayhead">${esc(fmtDay(day))}</div>
        <div class="games">${games.map(gameCard).join("")}</div>`).join("")}
    `;
    // scroll current week pill into view
    const active = $(".weeks a.active");
    if (active) active.scrollIntoView({ inline: "center", block: "nearest" });
    wireGameCards();
  }

  function statusLine(g) {
    const live = g.live || {};
    if (g.result) return `<span class="final">FINAL</span>`;
    if (live.state === "in") return `<span class="live">● LIVE ${esc(live.detail || "")}</span>`;
    return `<span>${esc(fmtTime(g.game.kickoff_utc))}</span>`;
  }

  function gameCard(g) {
    const p = g.prediction, home = g.home, away = g.away, res = g.result, pick = g.pick;
    const homeWin = p.predicted_winner === home.abbr;
    const scoreCell = (team, predScore, isWinner, actual) => {
      const a = actual != null ? `<span class="ascore">${actual}</span>` : (g.live.state === "in" && g.live[team === home ? "home_score" : "away_score"] != null ? `<span class="ascore">${g.live[team === home ? "home_score" : "away_score"]}</span>` : "");
      return `<span class="pscore ${isWinner ? "win" : ""}" title="Predicted score">${predScore}</span>${a}`;
    };
    const resultLoser = res ? (res.winner === home.abbr ? "away" : res.winner === away.abbr ? "home" : "") : "";
    const qbTag = (side) => g.qb[`${side}_qb_out`] ? `<span class="tag" title="${esc(g.qb[`${side}_qb_injury`]?.player || "")} listed ${esc(g.qb[`${side}_qb_injury`]?.status || "out")}">QB out</span>` : "";
    const pickBtn = (team) => {
      const picked = pick && pick.picked_team === team.abbr;
      let mark = "";
      if (picked && res) mark = res.winner === team.abbr ? " ✓" : " ✗";
      return `<button class="btn btn-sm pick ${picked ? "picked" : ""}" data-game="${esc(g.game.game_id)}" data-team="${esc(team.abbr)}" ${g.locked ? "disabled" : ""}>${picked ? "Your pick" : "Pick"} ${esc(team.abbr)}${mark}</button>`;
    };
    return `<div class="card game" data-game="${esc(g.game.game_id)}">
      <div class="status">${statusLine(g)}<span>${g.game.neutral_site ? "Neutral · " : ""}${esc(g.game.stadium || "")}</span></div>
      <div class="teamrow ${resultLoser === "away" ? "loser" : ""}">${logo(away)}
        <div class="tname">${esc(away.name)} <small>${away.abbr === p.predicted_winner ? "Model pick" : "&nbsp;"} ${qbTag("away")}</small></div>
        ${scoreCell(away, p.away_score, !homeWin, res ? res.away_score : null)}</div>
      <div class="teamrow ${resultLoser === "home" ? "loser" : ""}">${logo(home)}
        <div class="tname">${esc(home.name)} <small>${home.abbr === p.predicted_winner ? "Model pick" : "&nbsp;"} ${qbTag("home")}</small></div>
        ${scoreCell(home, p.home_score, homeWin, res ? res.home_score : null)}</div>
      ${probBar(away, home, p.home_win_prob)}
      <div class="pickrow">${pickBtn(away)}${pickBtn(home)}</div>
    </div>`;
  }

  function wireGameCards() {
    document.querySelectorAll(".game").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        location.hash = `#/game/${card.dataset.game}`;
      });
    });
    document.querySelectorAll("button.pick").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const gameId = btn.dataset.game, team = btn.dataset.team;
        const already = btn.classList.contains("picked");
        try {
          if (already) {
            await api(`/api/picks/${gameId}`, { method: "DELETE" });
            toast("Pick removed");
          } else {
            await api(`/api/picks/${gameId}`, { method: "PUT", body: JSON.stringify({ team }) });
            toast(`You picked ${team}`);
          }
          const card = btn.closest(".game");
          card.querySelectorAll("button.pick").forEach((b) => {
            const mine = !already && b.dataset.team === team;
            b.classList.toggle("picked", mine);
            b.textContent = `${mine ? "Your pick" : "Pick"} ${b.dataset.team}`;
          });
        } catch (err) { toast(err.message, true); }
      });
    });
  }

  // ---------------------------------------------------------------- game detail
  async function renderGame(gameId) {
    const g = await api(`/api/games/${encodeURIComponent(gameId)}`);
    const { home, away, prediction: p } = g;
    const factorRows = (pred) => {
      const maxAbs = Math.max(60, ...pred.factors.map((f) => Math.abs(f.elo)));
      return pred.factors.map((f) => {
        const w = Math.min(50, Math.abs(f.elo) / maxAbs * 50);
        const style = f.elo >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
        return `<tr><td><div>${esc(f.label)}</div><div class="muted small">${esc(f.note)}</div></td>
          <td style="width:28%"><div class="fbar"><i class="${f.elo < 0 ? "neg" : ""}" style="${style}"></i></div></td>
          <td class="num ${f.elo > 0 ? "pos" : f.elo < 0 ? "neg" : ""}">${sign(f.elo)} Elo</td>
          <td class="num ${f.points > 0 ? "pos" : f.points < 0 ? "neg" : ""}">${sign(f.points, 1)} pts</td></tr>`;
      }).join("") + `<tr class="total"><td>Total edge for ${esc(home.abbr)}</td><td></td>
          <td class="num">${sign(pred.total_elo_diff)} Elo</td><td class="num">${sign(pred.margin, 1)} pts</td></tr>`;
    };
    const injList = (list) => {
      const notable = list.filter((i) => !/^active$/i.test(i.status));
      if (!notable.length) return `<div class="muted small">No players listed as injured.</div>`;
      return `<div class="injuries">${notable.slice(0, 12).map((i) => `<div class="inj">
        <span class="muted small">${esc(i.position)}</span><span>${esc(i.player)}<div class="muted small">${esc(i.detail || "")}</div></span>
        <span class="st ${/out|reserve|doubtful/i.test(i.status) ? "neg" : "muted"}">${esc(i.status)}</span></div>`).join("")}
        ${notable.length > 12 ? `<div class="muted small">+${notable.length - 12} more</div>` : ""}</div>`;
    };
    const pick = g.pick;
    const res = g.result;
    const stored = g.stored_prediction;

    view.innerHTML = `
      <a class="muted small" href="#/week/${g.game.week}">‹ Week ${g.game.week}</a>
      <div class="card" style="margin-top:.5rem">
        <div class="muted small" style="text-align:center;margin-bottom:.75rem">
          ${esc(fmtDay(g.game.gameday))} · ${esc(fmtTime(g.game.kickoff_utc))} · ${esc(g.game.stadium || "")}${g.game.neutral_site ? " · Neutral site" : ""}
          ${res ? ` · <strong>FINAL ${esc(away.abbr)} ${res.away_score} – ${esc(home.abbr)} ${res.home_score}</strong>` : g.live.state === "in" ? ` · <span class="neg">LIVE ${esc(g.live.detail)}</span>` : ""}
        </div>
        <div class="matchhead">
          <a class="side" href="#/team/${away.abbr}">${logo(away)}<strong>${esc(away.name)}</strong><span class="muted small">${esc(g.game.away_qb || "")}</span><span class="bigscore" id="awayScore">${p.away_score}</span></a>
          <div class="vs">@</div>
          <a class="side" href="#/team/${home.abbr}">${logo(home)}<strong>${esc(home.name)}</strong><span class="muted small">${esc(g.game.home_qb || "")}</span><span class="bigscore" id="homeScore">${p.home_score}</span></a>
        </div>
        <div style="margin:1rem 0" id="bigProb">${probBar(away, home, p.home_win_prob, true)}</div>
        <div style="text-align:center" id="winnerLine">Model pick: <strong>${esc(p.predicted_winner === home.abbr ? home.name : away.name)}</strong> by ${Math.abs(p.margin).toFixed(1)} · ${pct(Math.max(p.home_win_prob, 1 - p.home_win_prob))} win probability</div>
        ${stored && stored.locked ? `<div class="muted small" style="text-align:center;margin-top:.4rem">Locked at kickoff: ${esc(stored.predicted_winner)} ${pct(stored.predicted_winner === home.abbr ? stored.home_win_prob : 1 - stored.home_win_prob)}, ${stored.away_score}–${stored.home_score}${stored.retroactive ? " (computed after kickoff)" : ""}</div>` : ""}
      </div>

      <div class="grid grid-2" style="margin-top:1rem">
        <div class="card">
          <h2>Why the model likes ${esc(p.predicted_winner)}</h2>
          <div class="muted small" style="margin-bottom:.5rem">Positive values favour the home team (${esc(home.abbr)}). 25 Elo ≈ 1 point.</div>
          <table class="factors" id="factorTable">${factorRows(p)}</table>
          <div class="muted small" style="margin-top:.5rem"><a href="#/how" class="pos">How this works →</a></div>
        </div>

        <div class="card">
          <h2>What if…</h2>
          <div class="muted small" style="margin-bottom:.6rem">Change the inputs and the prediction updates instantly. This does not change the stored model pick.</div>
          <div class="whatif" id="whatif">
            <label><span>${esc(g.game.home_qb || home.abbr + " starting QB")} (${esc(home.abbr)}) is OUT</span><input type="checkbox" name="home_qb_out" ${g.qb.home_qb_out ? "checked" : ""}></label>
            <label><span>${esc(g.game.away_qb || away.abbr + " starting QB")} (${esc(away.abbr)}) is OUT</span><input type="checkbox" name="away_qb_out" ${g.qb.away_qb_out ? "checked" : ""}></label>
            <label><span>QB-out impact</span><input type="range" name="qb_penalty" min="0" max="150" step="5" value="${p.inputs.qb_penalty}"><span class="val" data-for="qb_penalty">${p.inputs.qb_penalty}</span><span class="muted small">Elo</span></label>
            <label><span>Neutral site (no home-field edge)</span><input type="checkbox" name="neutral_site" ${p.inputs.neutral_site ? "checked" : ""}></label>
            <label><span>${esc(home.abbr)} rest days</span><input type="number" name="home_rest" min="3" max="21" value="${p.inputs.home_rest}"></label>
            <label><span>${esc(away.abbr)} rest days</span><input type="number" name="away_rest" min="3" max="21" value="${p.inputs.away_rest}"></label>
            <label><span>${esc(home.abbr)} other adjustment</span><input type="range" name="home_extra" min="-100" max="100" step="5" value="0"><span class="val" data-for="home_extra">0</span><span class="muted small">Elo</span></label>
            <label><span>${esc(away.abbr)} other adjustment</span><input type="range" name="away_extra" min="-100" max="100" step="5" value="0"><span class="val" data-for="away_extra">0</span><span class="muted small">Elo</span></label>
            <div class="row spread">
              <button class="btn btn-sm" id="resetWhatIf">Reset to model defaults</button>
              <span class="muted small" id="whatifStatus"></span>
            </div>
          </div>
          <div class="compare" style="margin-top:.75rem">
            <div class="box"><div class="muted small">Model default</div><div class="p">${esc(p.predicted_winner)} ${pct(Math.max(p.home_win_prob, 1 - p.home_win_prob))}</div><div class="muted small">${p.away_score}–${p.home_score}</div></div>
            <div class="box" id="scenarioBox"><div class="muted small">Your scenario</div><div class="p">${esc(p.predicted_winner)} ${pct(Math.max(p.home_win_prob, 1 - p.home_win_prob))}</div><div class="muted small">${p.away_score}–${p.home_score}</div></div>
          </div>
        </div>

        <div class="card">
          <h2>Your pick</h2>
          ${g.locked ? `<div class="muted small">Picks locked — this game has ${res ? "finished" : "kicked off"}.</div>` : `<div class="muted small" style="margin-bottom:.5rem">Pick the winner and optionally a final score. Compare it with the model on the My Picks page.</div>`}
          <div class="pickrow" style="margin:.5rem 0">
            <button class="btn ${pick?.picked_team === away.abbr ? "picked" : ""}" data-pickteam="${away.abbr}" ${g.locked ? "disabled" : ""}>${esc(away.name)}</button>
            <button class="btn ${pick?.picked_team === home.abbr ? "picked" : ""}" data-pickteam="${home.abbr}" ${g.locked ? "disabled" : ""}>${esc(home.name)}</button>
          </div>
          <div class="row" style="gap:.5rem">
            <label class="small muted">${esc(away.abbr)} <input type="number" id="pickAway" min="0" max="99" style="width:4rem" value="${pick?.away_score ?? ""}" ${g.locked ? "disabled" : ""}></label>
            <label class="small muted">${esc(home.abbr)} <input type="number" id="pickHome" min="0" max="99" style="width:4rem" value="${pick?.home_score ?? ""}" ${g.locked ? "disabled" : ""}></label>
            <button class="btn btn-primary btn-sm" id="savePick" ${g.locked ? "disabled" : ""}>Save</button>
            <button class="btn btn-sm" id="clearPick" ${g.locked || !pick ? "disabled" : ""}>Clear</button>
          </div>
          <div class="muted small" id="pickStatus" style="margin-top:.5rem">${pick ? `Saved: ${esc(pick.picked_team)}${pick.home_score != null ? ` (${pick.away_score}–${pick.home_score})` : ""} · ${pick.picked_team === p.predicted_winner ? "agrees with the model" : "disagrees with the model"}` : "No pick yet."}</div>
        </div>

        <div class="card">
          <h2>Injury report <span class="muted small" style="font-weight:500">(ESPN)</span></h2>
          <div class="grid grid-2">
            <div><h3>${esc(away.abbr)}</h3>${injList(g.injuries.away)}</div>
            <div><h3>${esc(home.abbr)}</h3>${injList(g.injuries.home)}</div>
          </div>
        </div>
      </div>`;

    // ---- what-if wiring
    const form = $("#whatif");
    let timer = null;
    const readOverrides = () => {
      const o = {};
      form.querySelectorAll("input").forEach((i) => {
        if (i.type === "checkbox") o[i.name] = i.checked;
        else if (i.value !== "") o[i.name] = Number(i.value);
      });
      return o;
    };
    const applyScenario = async () => {
      $("#whatifStatus").textContent = "Recalculating…";
      try {
        const np = await api(`/api/games/${encodeURIComponent(gameId)}/what-if`, { method: "POST", body: JSON.stringify({ overrides: readOverrides() }) });
        $("#factorTable").innerHTML = factorRows(np);
        $("#bigProb").innerHTML = probBar(away, home, np.home_win_prob, true);
        $("#homeScore").textContent = np.home_score;
        $("#awayScore").textContent = np.away_score;
        $("#winnerLine").innerHTML = `Scenario pick: <strong>${esc(np.predicted_winner === home.abbr ? home.name : away.name)}</strong> by ${Math.abs(np.margin).toFixed(1)} · ${pct(Math.max(np.home_win_prob, 1 - np.home_win_prob))} win probability`;
        const box = $("#scenarioBox");
        box.innerHTML = `<div class="muted small">Your scenario</div><div class="p">${esc(np.predicted_winner)} ${pct(Math.max(np.home_win_prob, 1 - np.home_win_prob))}</div><div class="muted small">${np.away_score}–${np.home_score}</div>`;
        box.classList.toggle("changed", Math.abs(np.home_win_prob - p.home_win_prob) > 0.0005);
        $("#whatifStatus").textContent = "";
      } catch (err) { $("#whatifStatus").textContent = err.message; }
    };
    form.addEventListener("input", (e) => {
      const v = form.querySelector(`.val[data-for="${e.target.name}"]`);
      if (v) v.textContent = e.target.value;
      clearTimeout(timer);
      timer = setTimeout(applyScenario, 150);
    });
    $("#resetWhatIf").addEventListener("click", () => {
      form.querySelector('[name=home_qb_out]').checked = g.qb.home_qb_out;
      form.querySelector('[name=away_qb_out]').checked = g.qb.away_qb_out;
      form.querySelector('[name=qb_penalty]').value = p.inputs.qb_penalty;
      form.querySelector('[name=neutral_site]').checked = p.inputs.neutral_site;
      form.querySelector('[name=home_rest]').value = p.inputs.home_rest;
      form.querySelector('[name=away_rest]').value = p.inputs.away_rest;
      form.querySelector('[name=home_extra]').value = 0;
      form.querySelector('[name=away_extra]').value = 0;
      form.querySelectorAll(".val").forEach((v) => (v.textContent = form.querySelector(`[name=${v.dataset.for}]`).value));
      applyScenario();
    });

    // ---- pick wiring
    let chosen = pick?.picked_team || null;
    view.querySelectorAll("[data-pickteam]").forEach((b) => b.addEventListener("click", () => {
      chosen = b.dataset.pickteam;
      view.querySelectorAll("[data-pickteam]").forEach((x) => x.classList.toggle("picked", x.dataset.pickteam === chosen));
    }));
    $("#savePick").addEventListener("click", async () => {
      if (!chosen) return toast("Choose a team first", true);
      const hs = $("#pickHome").value, as = $("#pickAway").value;
      try {
        const saved = await api(`/api/picks/${encodeURIComponent(gameId)}`, { method: "PUT", body: JSON.stringify({ team: chosen, home_score: hs === "" ? null : Number(hs), away_score: as === "" ? null : Number(as) }) });
        $("#pickStatus").textContent = `Saved: ${saved.picked_team}${saved.home_score != null ? ` (${saved.away_score}–${saved.home_score})` : ""} · ${saved.picked_team === p.predicted_winner ? "agrees with the model" : "disagrees with the model"}`;
        $("#clearPick").disabled = false;
        toast("Pick saved");
      } catch (err) { toast(err.message, true); }
    });
    $("#clearPick").addEventListener("click", async () => {
      try {
        await api(`/api/picks/${encodeURIComponent(gameId)}`, { method: "DELETE" });
        chosen = null;
        view.querySelectorAll("[data-pickteam]").forEach((x) => x.classList.remove("picked"));
        $("#pickHome").value = ""; $("#pickAway").value = "";
        $("#pickStatus").textContent = "No pick yet.";
        $("#clearPick").disabled = true;
        toast("Pick removed");
      } catch (err) { toast(err.message, true); }
    });
  }

  // ---------------------------------------------------------------- teams
  async function renderTeams() {
    const teams = await api("/api/teams");
    const byConf = { AFC: [], NFC: [] };
    teams.forEach((t) => byConf[t.conference].push(t));
    const card = (t) => `<a class="card teamcard" href="#/team/${t.abbr}">
        <span class="rank">${t.rank}</span>${logo(t)}
        <span><strong>${esc(t.name)}</strong><div class="muted small">${t.conference} ${t.division} · ${esc(t.record)}</div></span>
        <span class="elo">${Math.round(t.elo)}</span></a>`;
    view.innerHTML = `<h1>Power ratings</h1>
      <div class="muted small" style="margin-bottom:1rem">Elo rating entering week ${meta.current_week}. League average is 1500; a 25-point gap is worth about one point on the scoreboard.</div>
      <div class="grid grid-2">
        <div><h2>AFC</h2><div class="teams" style="grid-template-columns:1fr">${byConf.AFC.map(card).join("")}</div></div>
        <div><h2>NFC</h2><div class="teams" style="grid-template-columns:1fr">${byConf.NFC.map(card).join("")}</div></div>
      </div>`;
  }

  async function renderTeam(abbr) {
    const t = await api(`/api/teams/${encodeURIComponent(abbr)}`);
    const rec = t.record;
    const byWeek = new Map(t.schedule.map((s) => [s.game.week, s]));
    const rows = meta.weeks.map((w) => byWeek.get(w) || { bye: true, week: w }).map((s) => {
      if (s.bye) return `<tr><td class="num">${s.week}</td><td colspan="6" class="muted">Bye week</td></tr>`;
      const g = s.game, opp = s.opponent, p = s.prediction, r = s.result;
      const teamPred = s.is_home ? p.home_score : p.away_score, oppPred = s.is_home ? p.away_score : p.home_score;
      let resCell = `<span class="muted">—</span>`;
      if (r) {
        const tf = s.is_home ? r.home_score : r.away_score, of = s.is_home ? r.away_score : r.home_score;
        resCell = `<span class="${tf > of ? "pos" : tf < of ? "neg" : "muted"}"><strong>${tf > of ? "W" : tf < of ? "L" : "T"}</strong> ${tf}–${of}</span>`;
      }
      return `<tr class="link" data-game="${esc(g.game_id)}">
        <td class="num">${g.week}</td><td class="muted">${esc(fmtDayShort(g.gameday))}</td>
        <td><span class="row" style="gap:.4rem;flex-wrap:nowrap">${logo(opp, "logo").replace('class="logo"', 'class="logo" style="width:22px;height:22px"')} <span class="muted small">${s.is_home ? "vs" : "@"}</span> ${esc(opp.abbr)}</span></td>
        <td><span class="miniprob"><i style="width:${Math.round(s.team_win_prob * 100)}%"></i></span> <span class="mono small">${pct(s.team_win_prob)}</span></td>
        <td class="num">${teamPred}–${oppPred}</td>
        <td>${resCell}</td>
        <td>${s.pick ? `<span class="tag ${r ? (r.winner === s.pick.picked_team ? "ok" : "bad") : "info"}">${esc(s.pick.picked_team)}</span>` : ""}</td>
      </tr>`;
    }).join("");
    const notable = t.injuries.filter((i) => !/^active$/i.test(i.status));
    view.innerHTML = `
      <a class="muted small" href="#/teams">‹ All teams</a>
      <div class="teamhero" style="background:linear-gradient(135deg,#${esc(t.color)},#${esc(t.color)}cc 60%,#${esc(t.alt_color)}66);margin-top:.5rem">
        ${logo(t)}
        <div><h1>${esc(t.name)}</h1><div style="opacity:.9">${t.conference} ${t.division} · ${rec.wins}-${rec.losses}${rec.ties ? "-" + rec.ties : ""} · #${t.rank} power rating</div></div>
      </div>
      <div class="stats" style="margin:1rem 0">
        <div class="stat"><div class="v">${Math.round(t.elo)}</div><div class="k">Elo rating</div></div>
        <div class="stat"><div class="v">#${t.rank}</div><div class="k">League rank</div></div>
        <div class="stat"><div class="v">${t.avg_points_for ?? "–"}</div><div class="k">Pts for / game</div></div>
        <div class="stat"><div class="v">${t.avg_points_against ?? "–"}</div><div class="k">Pts against / game</div></div>
        <div class="stat"><div class="v">${t.form_margin != null ? sign(t.form_margin, 1) : "–"}</div><div class="k">Form (avg margin, last ${meta.params.form_games})</div></div>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <h2>Recent results</h2>
          <div class="form-strip">${t.recent.map((r) => `<span class="${r.won ? "w" : "l"}" title="${r.season} wk ${r.week} ${r.is_home ? "vs" : "@"} ${esc(r.opponent)} ${r.pf}-${r.pa}">${r.won ? "W" : "L"}</span>`).join("") || '<span class="muted small">No completed games in the data.</span>'}</div>
          <div class="muted small" style="margin-top:.4rem">Most recent game on the left; hover for details. Includes last season and playoffs.</div>
        </div>
        <div class="card">
          <h2>Injuries <span class="muted small" style="font-weight:500">(ESPN)</span></h2>
          ${notable.length ? `<div class="injuries">${notable.slice(0, 10).map((i) => `<div class="inj"><span class="muted small">${esc(i.position)}</span><span>${esc(i.player)}</span><span class="st ${/out|reserve|doubtful/i.test(i.status) ? "neg" : "muted"}">${esc(i.status)}</span></div>`).join("")}</div>` : `<div class="muted small">No players listed as injured.</div>`}
        </div>
      </div>
      <div class="card" style="margin-top:1rem">
        <h2>${meta.season} schedule &amp; predictions</h2>
        <div class="table-wrap"><table class="table">
          <thead><tr><th class="num">Wk</th><th>Date</th><th>Opponent</th><th>Win prob</th><th class="num">Pred.</th><th>Result</th><th>Pick</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
      </div>`;
    view.querySelectorAll("tr.link").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/game/${tr.dataset.game}`)));
  }

  // ---------------------------------------------------------------- picks
  async function renderPicks() {
    const data = await api("/api/picks");
    const ag = data.agreement;
    const rows = data.picks.map((r) => {
      const g = r.game;
      const yourTag = r.you_correct == null ? "info" : r.you_correct ? "ok" : "bad";
      const modelTag = r.model_correct == null ? "info" : r.model_correct ? "ok" : "bad";
      return `<tr class="link" data-game="${esc(g.game_id)}">
        <td class="num">${g.week}</td>
        <td>${esc(r.away.abbr)} @ ${esc(r.home.abbr)}</td>
        <td><span class="tag ${yourTag}">${esc(r.pick.picked_team)}</span>${r.pick.home_score != null ? ` <span class="muted small">${r.pick.away_score}–${r.pick.home_score}</span>` : ""}</td>
        <td>${r.model_pick ? `<span class="tag ${modelTag}">${esc(r.model_pick)}</span> <span class="muted small">${pct(r.model_pick === r.home.abbr ? r.model_prob : 1 - r.model_prob)}</span>` : "—"}</td>
        <td>${r.agrees == null ? "—" : r.agrees ? '<span class="pos">Agree</span>' : '<span class="neg">Disagree</span>'}</td>
        <td>${r.result ? `${r.result.away_score}–${r.result.home_score}` : '<span class="muted">Pending</span>'}</td>
      </tr>`;
    }).join("");
    view.innerHTML = `<h1>My picks</h1>
      <div class="stats" style="margin:.75rem 0 1rem">
        <div class="stat"><div class="v">${ag.picks}</div><div class="k">Picks made</div></div>
        <div class="stat"><div class="v">${ag.agree_rate == null ? "–" : pct(ag.agree_rate)}</div><div class="k">Agree with model</div></div>
        <div class="stat"><div class="v">${ag.compared - ag.agree}</div><div class="k">Fades of the model</div></div>
      </div>
      ${data.picks.length ? `<div class="card"><div class="table-wrap"><table class="table">
        <thead><tr><th class="num">Wk</th><th>Game</th><th>You</th><th>Model</th><th></th><th>Final</th></tr></thead><tbody>${rows}</tbody></table></div></div>`
        : `<div class="card muted">You haven't made any picks yet. Head to the <a class="pos" href="#/week">schedule</a> and tap a team.</div>`}`;
    view.querySelectorAll("tr.link").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/game/${tr.dataset.game}`)));
  }

  // ---------------------------------------------------------------- leaderboard
  async function renderLeaderboard() {
    const lb = await api("/api/leaderboard");
    const leader = lb.rows.find((r) => r.accuracy != null);
    const cards = lb.rows.map((r) => `<div class="card lbcard ${leader && r.key === leader.key && r.graded ? "leader" : ""}">
        <div class="acc">${r.accuracy == null ? "–" : pct(r.accuracy)}</div>
        <div class="nm">${esc(r.name)}</div>
        <div class="muted small">${r.correct} / ${r.graded} correct</div>
        <div class="muted small">Brier ${r.brier == null ? "–" : r.brier.toFixed(3)}${r.avg_score_error != null ? ` · avg score miss ${r.avg_score_error}` : ""}</div>
        ${r.retroactive ? `<div class="muted small">${r.retroactive} graded retroactively*</div>` : ""}
      </div>`).join("");
    const weekly = lb.weekly.length ? `<div class="card" style="margin-top:1rem"><h2>Week by week</h2><div class="table-wrap"><table class="table">
        <thead><tr><th class="num">Week</th><th class="num">Games</th><th class="num">Model</th><th class="num">You</th><th class="num">Always home</th></tr></thead>
        <tbody>${lb.weekly.map((w) => `<tr><td class="num">${w.week}</td><td class="num">${w.games}</td>
          <td class="num">${w.model_correct}/${w.games}</td><td class="num">${w.you_picks ? `${w.you_correct}/${w.you_picks}` : "—"}</td><td class="num">${w.home_correct}/${w.games}</td></tr>`).join("")}</tbody></table></div></div>` : "";
    view.innerHTML = `<h1>Leaderboard</h1>
      <div class="muted small" style="margin-bottom:1rem">Graded on final results from ${lb.season} regular-season games. The model's pick is frozen at kickoff so it can't be graded on hindsight. Brier score: lower is better (0 = perfect, 0.25 = coin flip).</div>
      <div class="lb">${cards}</div>
      ${weekly}
      ${lb.rows[0].graded === 0 ? `<div class="card muted" style="margin-top:1rem">No games have finished yet this season. Come back after week ${lb.current_week} kicks off — results sync automatically from nflverse and ESPN.</div>` : ""}
      ${lb.rows.some((r) => r.retroactive) ? `<div class="muted small" style="margin-top:.5rem">* A retroactive grade means the app first computed that prediction after the game had started. The rating snapshot used still excludes that game's result, but treat those as back-tests, not live picks.</div>` : ""}`;
  }

  // ---------------------------------------------------------------- how
  async function renderHow() {
    const p = meta.params, d = meta.data;
    view.innerHTML = `<div class="prose">
      <h1>How this works</h1>
      <p>The model is a transparent, five-factor power-rating system. There is no machine learning and no betting-market input: every number on a game page can be traced back to the formula below.</p>

      <h2>1. Team strength: Elo ratings</h2>
      <p>Every team starts at <code>1500</code>. After each game the winner takes rating points from the loser:</p>
      <p><code>Δ = K × MOV × (actual − expected)</code>, with <code>K = ${p.elo_k}</code>, <code>expected = 1 / (1 + 10^((opp − team) / 400))</code>.</p>
      <p><code>MOV = ln(|margin| + 1) × 2.2 / (0.001 × favourite's edge + 2.2)</code> makes blowouts count more but discounts big wins by heavy favourites. At the start of every season each team is pulled <strong>${Math.round(p.season_regression * 100)}%</strong> of the way back to 1500 to reflect roster turnover. Ratings are replayed from the 2022 season onward, including playoffs.</p>

      <h2>2. Situational factors</h2>
      <table>
        <tr><th>Factor</th><th>Rule</th><th>Typical size</th></tr>
        <tr><td>Home field</td><td>+${p.elo_home_advantage} Elo to the home team; 0 at neutral sites</td><td>≈ ${(p.elo_home_advantage / p.elo_points_per_point).toFixed(1)} pts</td></tr>
        <tr><td>Rest</td><td>${p.rest_points_per_day} Elo per rest day above/below 7, clamped to [${p.rest_min}, +${p.rest_max}]</td><td>bye ≈ +1 pt, Thursday game ≈ −0.5 pt</td></tr>
        <tr><td>Recent form</td><td>${p.form_points_per_margin} Elo per point of average margin over the last ${p.form_games} games, capped at ±${p.form_max}</td><td>up to ±${(p.form_max / p.elo_points_per_point).toFixed(1)} pts</td></tr>
        <tr><td>Starting QB</td><td>−${p.qb_out_penalty} Elo when the projected starter (from nflverse) is listed Out / Doubtful / IR on ESPN's injury report</td><td>≈ ${(p.qb_out_penalty / p.elo_points_per_point).toFixed(1)} pts</td></tr>
      </table>

      <h2>3. Win probability and score</h2>
      <p>All factors are summed into one Elo difference <code>D</code> (positive favours the home team). Then</p>
      <p><code>P(home wins) = 1 / (1 + 10^(−D / 400))</code> and <code>expected margin = D / ${p.elo_points_per_point}</code>.</p>
      <p>The expected total is built from each team's points scored and allowed over its last ${p.scoring_games} games (offence vs. the opponent's defence, averaged). The margin is then split around that total and rounded; ties after rounding are broken toward the favourite so the score always agrees with the pick.</p>

      <h2>4. What the what-if panel does</h2>
      <p>Every factor is an input. Toggling a QB out, changing rest days, making the site neutral or adding a manual Elo adjustment re-runs the same formula server-side. Scenarios are never stored; the model's own pick is frozen in the database at kickoff and graded on the leaderboard.</p>

      <h2>Data sources</h2>
      <ul>
        <li><strong>nflverse <code>games.csv</code></strong> — schedule, final scores, rest days, projected starting QBs and ESPN ids for every game. Currently loaded from: <code>${esc(d.origin)}</code>${d.fetched_at ? ` (fetched ${esc(new Date(d.fetched_at).toLocaleString())})` : ""}.</li>
        <li><strong>ESPN public site API</strong> — live game status/scores and the injury report (${d.injuries_count} entries loaded). ${d.espn_live ? "Live." : "Disabled (offline mode)."}</li>
      </ul>

      <h2>Known limitations</h2>
      <ul>
        <li>Elo only sees final scores, so it is slow to react to trades, coaching changes and rookies, and treats a 2025 playoff run and a 2026 week-1 game as the same kind of evidence.</li>
        <li>Injuries beyond the quarterback are not modelled automatically; use the manual adjustment slider.</li>
        <li>Predicted scores are expected values, not most-likely outcomes, so they land on "unlikely" football scores (e.g. 24–22) more often than real games do.</li>
        <li>Kickoff times from nflverse are US Eastern and converted assuming daylight time, which is correct for the entire regular season.</li>
        <li>Historically this style of model picks the winner around 63–67% of the time — respectable but nowhere near certain.</li>
      </ul>
      <p class="muted small">This app is for entertainment and learning. It is not betting advice and no claim is made about accuracy versus betting markets.</p>
    </div>`;
  }

  // ---------------------------------------------------------------- boot
  $("#refreshBtn").addEventListener("click", async () => {
    const btn = $("#refreshBtn");
    btn.disabled = true; btn.textContent = "Refreshing…";
    try {
      meta = await api("/api/refresh", { method: "POST" });
      toast("Data refreshed");
      route();
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; btn.textContent = "↻ Refresh"; }
  });
  window.addEventListener("hashchange", route);
  api("/api/meta").then((m) => {
    meta = m;
    $("#footerData").textContent = `Data: nflverse games.csv (${m.data.origin}) + ESPN public API · Model ${m.model_version} · ${m.season} season, week ${m.current_week}`;
  }).catch(() => {}).finally(route);
})();

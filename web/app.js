const $ = s => document.querySelector(s);
const STEPS = ["INGEST", "TRANSCRIPT", "SCENE", "DIRECT", "RENDER"];
let model = "auto", caps = true, job = null, clips = [], pickIdx = 0;

const show = id => document.querySelectorAll(".screen").forEach(s => s.classList.toggle("on", s.id === id));

fetch("/api/brains").then(r => r.json()).then(b => $("#brainTag").textContent = `local · brain: ${b.default}`);

$("#models").onclick = e => {
  const m = e.target.dataset.m; if (!m) return;
  model = m; $("#models").querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === e.target));
};
$("#capToggle").onclick = e => { caps = !caps; e.target.classList.toggle("on", caps); e.target.textContent = caps ? "on" : "off"; };
$("#file").onchange = e => { if (e.target.files[0]) $("#src").value = e.target.files[0].name; };

$("#cutForm").onsubmit = async e => {
  e.preventDefault();
  const source = $("#src").value.trim();
  const r = await fetch("/api/cut", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, model, captions: caps }) });
  job = (await r.json()).id;
  startWatchingJob(job, source);
};

function startWatchingJob(jid, source) {
  job = jid;
  $("#procSrc").textContent = source || jid;
  $("#steps").innerHTML = STEPS.map(s => `<li data-s="${s}"><span class="mono">${s}</span><span class="bar"><i></i></span><span class="mono dim">0%</span></li>`).join("");
  show("processing");
  const es = new EventSource(`/api/jobs/${jid}/events`);
  es.onmessage = ev => {
    const st = JSON.parse(ev.data);
    for (const s of STEPS) {
      const li = $(`#steps [data-s=${s}]`), p = st.progress[s];
      if (!li) continue;
      li.querySelector("i").style.width = p * 100 + "%";
      li.lastChild.textContent = Math.round(p * 100) + "%";
      li.classList.toggle("done", p >= 1);
    }
    $("#log").textContent = st.log.join("\n") + (st.error ? `\n\nerror: ${st.error}` : "");
    if (st.status === "done") { es.close(); results(st.clips); }
    if (st.status !== "running") es.close();
  };
}

$("#cancelJob").onclick = async () => {
  if (!job) return;
  await fetch(`/api/jobs/${job}/cancel`, { method: "POST" });
  $("#log").textContent += "\n\ncancelling…";
};

function results(list) {
  clips = list;
  $("#strip").innerHTML = list.map((c, i) => `<div class="card" data-i="${i}"><img src="${c.thumb}" alt=""><div><b>${c.score}</b><span>${fmt(c.duration)}</span></div></div>`).join("");
  $("#strip").onclick = e => { const c = e.target.closest(".card"); if (c) pick(+c.dataset.i); };
  $("#dlAll").href = `/api/jobs/${job}/zip`;
  $("#uploadMsg").textContent = "";
  show("results"); pick(0);
}

function pick(i) {
  pickIdx = i;
  const c = clips[i];
  document.querySelectorAll(".card").forEach((el, j) => el.classList.toggle("on", i === j));
  $("#player").src = c.mp4; $("#dlMp4").href = c.mp4; $("#dlSrt").href = c.srt;
  $("#score").textContent = c.score; $("#why").textContent = c.why;
  $("#bars").innerHTML = Object.entries(c.sub).map(([k, v]) => `<div class="sb"><span class="dim">${k.toUpperCase()}</span><span class="bar"><i style="width:${v * 10}%"></i></span><span>${v}</span></div>`).join("");
  document.querySelectorAll(".copy").forEach(el => {
    const v = c[el.dataset.k]; el.querySelector("p").textContent = Array.isArray(v) ? v.join(" ") : v;
    el.querySelector("button").onclick = () => { navigator.clipboard.writeText(el.querySelector("p").textContent); el.querySelector("button").textContent = "COPIED"; setTimeout(() => el.querySelector("button").textContent = "COPY", 1200); };
  });
}
const fmt = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
$("#again").onclick = () => show("input");

$("#uploadBtn").onclick = async () => {
  if (!job || !clips[pickIdx]) return;
  const btn = $("#uploadBtn");
  btn.disabled = true; btn.textContent = "UPLOADING…";
  $("#uploadMsg").textContent = "private by default — review on YouTube before going public";
  try {
    const r = await fetch("/api/upload", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job, clip: clips[pickIdx].name, privacy: "private" }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "upload failed");
    $("#uploadMsg").innerHTML = `uploaded · <a href="${j.url}" target="_blank" rel="noopener">${j.id}</a> (${j.privacy})`;
  } catch (e) {
    $("#uploadMsg").textContent = String(e.message || e);
  } finally {
    btn.disabled = false; btn.textContent = "UPLOAD";
  }
};

// ── YouTube connect ───────────────────────────────────────────
async function refreshYt() {
  const r = await fetch("/api/youtube"); const j = await r.json();
  const btn = $("#ytBtn");
  if (j.connected) {
    btn.textContent = "YOUTUBE ✓";
    btn.classList.add("on");
  } else if (!j.has_client_secret) {
    btn.textContent = "ADD client_secret.json";
    btn.classList.remove("on");
  } else {
    btn.textContent = j.oauth?.state === "running" ? "SIGN IN…" : "CONNECT YOUTUBE";
    btn.classList.remove("on");
  }
}
$("#ytBtn").onclick = async () => {
  const st = await (await fetch("/api/youtube")).json();
  if (st.connected) {
    if (confirm("Disconnect YouTube?")) {
      await fetch("/api/youtube/disconnect", { method: "POST" });
      refreshYt();
    }
    return;
  }
  if (!st.has_client_secret) {
    alert("Drop client_secret.json in the offcut folder (see README → Connect YouTube), then click again.");
    return;
  }
  const r = await fetch("/api/youtube/connect", { method: "POST" });
  const j = await r.json();
  if (!r.ok) { alert(j.detail || "connect failed"); return; }
  alert(j.message || "Check your browser for Google sign-in.");
  const t = setInterval(async () => {
    const s = await (await fetch("/api/youtube")).json();
    if (s.connected || s.oauth?.state === "error" || s.oauth?.state === "done") {
      clearInterval(t); refreshYt();
      if (s.oauth?.error) alert(s.oauth.error);
    }
  }, 1500);
};
refreshYt();

// ── Channel watch ─────────────────────────────────────────────
async function refreshWatch() {
  const r = await fetch("/api/watch"); const j = await r.json();
  $("#pollMins").value = j.poll_minutes;
  $("#watchList").innerHTML = j.channels.length ? j.channels.map(c => `
    <li>
      <div>
        <b>${esc(c.title)}</b>
        <span class="mono dim">${esc(c.handle || c.id)}</span>
      </div>
      <label class="chip" style="cursor:pointer">
        <input type="checkbox" data-au="${c.id}" ${c.auto_upload ? "checked" : ""} style="accent-color:var(--hot)"> auto-upload
      </label>
      <button type="button" class="btn ghost mono" data-rm="${c.id}">REMOVE</button>
    </li>`).join("") : `<li class="dim mono">no channels yet</li>`;
  $("#autoJobs").innerHTML = j.auto_jobs.length ? j.auto_jobs.map(a => `
    <li>
      <div><b>${esc(a.title || a.video_id)}</b>
        <span class="mono dim">${esc(a.channel)} · ${a.status} · ${a.job_id}${a.auto_upload ? " · ↑" : ""}</span>
      </div>
      ${a.status === "done" ? `<button type="button" class="btn ghost mono" data-open="${a.job_id}">OPEN</button>` : ""}
    </li>`).join("") : `<li class="dim mono">none yet</li>`;
}
const esc = s => String(s || "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));

$("#watchForm").onsubmit = async e => {
  e.preventDefault();
  const url = $("#watchUrl").value.trim();
  const auto_upload = $("#watchAutoUp").checked;
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await fetch("/api/watch", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, auto_upload }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "failed");
    $("#watchUrl").value = "";
    await refreshWatch();
  } catch (err) { alert(err.message || err); }
  finally { btn.disabled = false; btn.textContent = "ADD"; }
};

$("#watchList").onclick = async e => {
  const rm = e.target.closest("[data-rm]");
  if (rm) {
    await fetch(`/api/watch/${rm.dataset.rm}`, { method: "DELETE" });
    refreshWatch(); return;
  }
  const au = e.target.closest("input[data-au]");
  if (au) {
    await fetch(`/api/watch/${au.dataset.au}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_upload: au.checked }) });
  }
};

$("#autoJobs").onclick = async e => {
  const open = e.target.closest("[data-open]");
  if (!open) return;
  // reopen a finished job from disk via its events (may 404 if server restarted — fall back to files)
  const jid = open.dataset.open;
  const r = await fetch(`/api/jobs/${jid}/events`);
  if (r.ok) {
    // SSE won't replay finished jobs registered after restart; try zip listing via job.json
  }
  // load job.json from files
  const jr = await fetch(`/files/${jid}/job.json`);
  if (!jr.ok) { alert("job gone from memory — open files in out/" + jid); return; }
  const st = await jr.json();
  job = jid;
  results(st.clips || []);
};

$("#pollMins").onchange = async () => {
  await fetch("/api/watch/poll", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ minutes: +$("#pollMins").value || 30 }) });
};
$("#pollNow").onclick = async () => {
  $("#pollNow").textContent = "…";
  const r = await fetch("/api/watch/poll-now", { method: "POST" });
  const j = await r.json();
  $("#pollNow").textContent = "POLL NOW";
  await refreshWatch();
  if (j.started?.length) {
    const first = j.started[0];
    if (confirm(`Started ${j.started.length} job(s). Open ${first.title}?`)) {
      startWatchingJob(first.job_id, first.title);
    }
  } else {
    alert("No new uploads.");
  }
};

refreshWatch();
setInterval(refreshWatch, 15000);

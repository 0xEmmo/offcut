const $ = s => document.querySelector(s);
const CUT_STEPS = ["INGEST", "TRANSCRIPT", "SCENE", "DIRECT", "RENDER"];
let model = "auto", caps = true, job = null, clips = [], pickIdx = 0;
let mode = "cut", recapLen = 1, recapFmt = "9:16", recapCaps = true, recapSpoil = false;
let recapPath = null, recapUploading = false;

const show = id => document.querySelectorAll(".screen").forEach(s => s.classList.toggle("on", s.id === id));

fetch("/api/brains").then(r => r.json()).then(b => $("#brainTag").textContent = `local · brain: ${b.default}`);

// ── Mode tabs ─────────────────────────────────────────────────
$("#modeTabs").onclick = e => {
  const t = e.target.closest("[data-mode]"); if (!t) return;
  mode = t.dataset.mode;
  $("#modeTabs").querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === t));
  $("#cutPane").hidden = mode !== "cut";
  $("#recapPane").hidden = mode !== "recap";
};

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
  startWatchingJob(job, source, CUT_STEPS);
};

// ── Recap ─────────────────────────────────────────────────────
fetch("/api/recap/options").then(r => r.json()).then(o => {
  $("#recapVoice").innerHTML = (o.voices || []).map(v =>
    `<option value="${v.id}">${v.label}</option>`).join("");
});

$("#recapLen").onclick = e => {
  const b = e.target.closest("[data-l]"); if (!b) return;
  recapLen = +b.dataset.l;
  $("#recapLen").querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === b));
};
$("#recapFmt").onclick = e => {
  const b = e.target.closest("[data-f]"); if (!b) return;
  recapFmt = b.dataset.f;
  $("#recapFmt").querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === b));
};
$("#recapCap").onclick = e => {
  recapCaps = !recapCaps;
  e.target.classList.toggle("on", recapCaps);
  e.target.textContent = recapCaps ? "on" : "off";
};
$("#recapSpoil").onclick = e => {
  recapSpoil = !recapSpoil;
  e.target.classList.toggle("on", recapSpoil);
  e.target.textContent = recapSpoil ? "spoil" : "hide";
};

$("#recapFile").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  $("#recapName").value = f.name + " · uploading…";
  recapUploading = true; recapPath = null;
  const fd = new FormData(); fd.append("file", f);
  try {
    const r = await fetch("/api/upload-file", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "upload failed");
    recapPath = j.path;
    $("#recapName").value = f.name;
  } catch (err) {
    $("#recapName").value = "";
    alert(err.message || err);
  } finally { recapUploading = false; }
};

$("#recapForm").onsubmit = async e => {
  e.preventDefault();
  if (recapUploading) { alert("Still uploading…"); return; }
  if (!recapPath) { alert("Upload a movie file first."); return; }
  const btn = $("#recapGo"); btn.disabled = true; btn.textContent = "…";
  try {
    const r = await fetch("/api/recap", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: recapPath, length: recapLen, fmt: recapFmt,
        voice: $("#recapVoice").value, spoilers: recapSpoil, captions: recapCaps,
      }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "recap failed");
    startWatchingJob(j.id, $("#recapName").value || "recap", null);
  } catch (err) { alert(err.message || err); }
  finally { btn.disabled = false; btn.textContent = "RECAP IT"; }
};

function startWatchingJob(jid, source, steps) {
  job = jid;
  $("#procSrc").textContent = source || jid;
  const initial = steps || CUT_STEPS;
  $("#steps").innerHTML = initial.map(s => `<li data-s="${s}"><span class="mono">${s}</span><span class="bar"><i></i></span><span class="mono dim">0%</span></li>`).join("");
  show("processing");
  let stepped = !!steps;
  const es = new EventSource(`/api/jobs/${jid}/events`);
  es.onmessage = ev => {
    const st = JSON.parse(ev.data);
    if (!stepped && st.steps && st.steps.length) {
      $("#steps").innerHTML = st.steps.map(s => `<li data-s="${s}"><span class="mono">${s}</span><span class="bar"><i></i></span><span class="mono dim">0%</span></li>`).join("");
      stepped = true;
    }
    const keys = st.steps || Object.keys(st.progress || {});
    for (const s of keys) {
      const li = document.querySelector(`#steps [data-s="${s}"]`), p = st.progress[s];
      if (!li || p == null) continue;
      li.querySelector("i").style.width = p * 100 + "%";
      li.lastChild.textContent = Math.round(p * 100) + "%";
      li.classList.toggle("done", p >= 1);
    }
    $("#log").textContent = st.log.join("\n") + (st.error ? `\n\nerror: ${st.error}` : "");
    if (st.status === "done") { es.close(); results(st.clips, st.fmt); }
    if (st.status !== "running") es.close();
  };
}

$("#cancelJob").onclick = async () => {
  if (!job) return;
  await fetch(`/api/jobs/${job}/cancel`, { method: "POST" });
  $("#log").textContent += "\n\ncancelling…";
};

function results(list, fmt) {
  clips = list;
  $("#strip").innerHTML = list.map((c, i) => `<div class="card" data-i="${i}"><img src="${c.thumb}" alt=""><div><b>${c.score}</b><span>${fmtDur(c.duration)}</span></div></div>`).join("");
  $("#strip").onclick = e => { const c = e.target.closest(".card"); if (c) pick(+c.dataset.i); };
  $("#dlAll").href = `/api/jobs/${job}/zip`;
  $("#uploadMsg").textContent = "";
  const ratio = (fmt || (list[0] && list[0].fmt) || "9:16");
  $("#player").style.aspectRatio = ratio === "16:9" ? "16/9" : "9/16";
  show("results"); pick(0);
}

function pick(i) {
  pickIdx = i;
  const c = clips[i];
  document.querySelectorAll(".card").forEach((el, j) => el.classList.toggle("on", i === j));
  $("#player").src = c.mp4; $("#dlMp4").href = c.mp4; $("#dlSrt").href = c.srt;
  $("#score").textContent = c.score; $("#why").textContent = c.why;
  $("#bars").innerHTML = Object.entries(c.sub || {}).map(([k, v]) => `<div class="sb"><span class="dim">${k.toUpperCase()}</span><span class="bar"><i style="width:${v * 10}%"></i></span><span>${v}</span></div>`).join("");
  document.querySelectorAll(".copy").forEach(el => {
    const v = c[el.dataset.k]; el.querySelector("p").textContent = Array.isArray(v) ? v.join(" ") : v;
    el.querySelector("button").onclick = () => { navigator.clipboard.writeText(el.querySelector("p").textContent); el.querySelector("button").textContent = "COPIED"; setTimeout(() => el.querySelector("button").textContent = "COPY", 1200); };
  });
}
const fmtDur = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
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
  const jid = open.dataset.open;
  const jr = await fetch(`/files/${jid}/job.json`);
  if (!jr.ok) { alert("job gone from memory — open files in out/" + jid); return; }
  const st = await jr.json();
  job = jid;
  results(st.clips || [], st.fmt);
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
      startWatchingJob(first.job_id, first.title, CUT_STEPS);
    }
  } else {
    alert("No new uploads.");
  }
};

refreshWatch();
setInterval(refreshWatch, 15000);

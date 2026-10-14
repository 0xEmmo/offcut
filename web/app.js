const $ = s => document.querySelector(s);
const STEPS = ["INGEST", "TRANSCRIPT", "SCENE", "DIRECT", "RENDER"];
let model = "auto", caps = true, job = null, clips = [];

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
  $("#procSrc").textContent = source;
  $("#steps").innerHTML = STEPS.map(s => `<li data-s="${s}"><span class="mono">${s}</span><span class="bar"><i></i></span><span class="mono dim">0%</span></li>`).join("");
  show("processing");
  const es = new EventSource(`/api/jobs/${job}/events`);
  es.onmessage = ev => {
    const st = JSON.parse(ev.data);
    for (const s of STEPS) {
      const li = $(`#steps [data-s=${s}]`), p = st.progress[s];
      li.querySelector("i").style.width = p * 100 + "%";
      li.lastChild.textContent = Math.round(p * 100) + "%";
      li.classList.toggle("done", p >= 1);
    }
    $("#log").textContent = st.log.join("\n") + (st.error ? `\n\nerror: ${st.error}` : "");
    if (st.status === "done") { es.close(); results(st.clips); }
    if (st.status !== "running") es.close();
  };
};

function results(list) {
  clips = list;
  $("#strip").innerHTML = list.map((c, i) => `<div class="card" data-i="${i}"><img src="${c.thumb}" alt=""><div><b>${c.score}</b><span>${fmt(c.duration)}</span></div></div>`).join("");
  $("#strip").onclick = e => { const c = e.target.closest(".card"); if (c) pick(+c.dataset.i); };
  $("#dlAll").href = `/api/jobs/${job}/zip`;
  show("results"); pick(0);
}

function pick(i) {
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

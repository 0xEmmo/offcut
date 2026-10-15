(() => {
  const $ = s => document.querySelector(s);
  let mins = 10, timer = null;
  $("#docuLen").onclick = e => {
    const b = e.target.closest("[data-l]"); if (!b) return;
    mins = +b.dataset.l; $("#docuLen").querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === b));
  };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  function draw(st) {
    $("#docuSteps").innerHTML = Object.entries(st.progress).map(([k, v]) =>
      `<li>${k} <span class="dim">${Math.round(v * 100)}%</span></li>`).join("");
    $("#docuLog").textContent = (st.log || []).join("\n") + (st.error ? "\nERROR: " + st.error : "");
    $("#docuOut").innerHTML = (st.videos || []).map(v => `
      <div class="watch" style="margin-top:16px">
        <p class="mono dim">${v.vertical ? "SHORT 9:16" : "LONG 16:9"} · ${(v.duration / 60).toFixed(1)} min</p>
        <p><b>${esc(v.title)}</b></p>
        <video src="${v.mp4}" controls playsinline preload="metadata" poster="${v.thumb}" style="max-width:100%;max-height:420px"></video>
        <div class="btns" style="margin-top:8px">
          <a class="btn hot mono" href="${v.mp4}" download>DOWNLOAD MP4</a>
          <a class="btn ghost mono" href="${v.thumb}" download>THUMBNAIL</a>
          <a class="btn ghost mono" href="/files/${st.id}/${v.name}.txt" download>METADATA</a>
          ${st.status === "done" ? `<button type="button" class="btn ghost mono" data-up="${v.name}">UPLOAD PRIVATE</button>` : ""}
        </div>
        <p class="mono dim" id="up-${v.name}"></p>
      </div>`).join("");
  }
  async function poll(id) {
    const st = await (await fetch(`/api/docu/${id}`)).json();
    draw(st); window._docu = st.id;
    if (st.status === "running") timer = setTimeout(() => poll(id), 1500);
    else { $("#docuGo").disabled = false; $("#docuGo").textContent = "MAKE IT"; }
  }
  $("#docuForm").onsubmit = async e => {
    e.preventDefault();
    let script = null; const raw = $("#docuScript").value.trim();
    if (raw) { try { script = JSON.parse(raw); } catch { alert("Script JSON is not valid."); return; } }
    $("#docuGo").disabled = true; $("#docuGo").textContent = "…"; $("#docuOut").innerHTML = ""; $("#docuProg").hidden = false;
    const r = await fetch("/api/docu", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: $("#docuTopic").value, minutes: mins, script }) });
    const j = await r.json();
    if (!r.ok) { alert(j.detail || "failed"); $("#docuGo").disabled = false; return; }
    clearTimeout(timer); poll(j.id);
  };
  $("#docuOut").onclick = async e => {
    const b = e.target.closest("[data-up]"); if (!b) return;
    const msg = $(`#up-${b.dataset.up}`); b.disabled = true; msg.textContent = "uploading (private)…";
    const r = await fetch(`/api/docu/${window._docu}/upload`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: b.dataset.up, privacy: "private" }) });
    const j = await r.json();
    msg.innerHTML = r.ok ? `uploaded: <a href="${j.url}" target="_blank">${j.url}</a> · thumbnail ${esc(j.thumbnail)}` : "error: " + esc(j.detail);
    b.disabled = r.ok;
  };
})();

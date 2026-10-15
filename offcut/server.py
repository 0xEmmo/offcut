import asyncio, json, threading, zipfile, io, uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .pipeline import Job, OUT
from .recap import RecapJob, UPLOADS, VOICES, LENGTH_TARGETS
from . import brain, watch, youtube_upload as yt, docu

JOBS: dict[str, object] = {}

def uuid_hex():
    return uuid.uuid4().hex[:10]

WEB = Path(__file__).resolve().parent.parent / "web"
OUT.mkdir(exist_ok=True)


def _register(job: Job):
    JOBS[job.id] = job


@asynccontextmanager
async def lifespan(app):
    watch.start_poller(_register)
    yield
    watch.stop_poller()


app = FastAPI(title="offcut", lifespan=lifespan)


class CutReq(BaseModel):
    source: str
    model: str = "auto"
    captions: bool = True
    count: int = 4


class WatchAdd(BaseModel):
    url: str
    auto_upload: bool = False


class WatchToggle(BaseModel):
    auto_upload: bool


class PollMins(BaseModel):
    minutes: int


class UploadReq(BaseModel):
    job_id: str
    clip: str  # e.g. clip_01
    privacy: str = "private"


@app.get("/api/brains")
def brains():
    return {"default": brain.available()}


@app.post("/api/cut")
def cut(req: CutReq):
    job = Job(req.source.strip(), req.model, req.captions, max(1, min(req.count, 8)))
    JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id}


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404)
    job.cancel()
    return {"ok": True, "status": job.status}


@app.get("/api/jobs/{jid}/events")
async def events(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404)
    async def gen():
        last = None
        while True:
            st = json.dumps(job.state())
            if st != last:
                yield f"data: {st}\n\n"; last = st
            if job.status != "running": break
            await asyncio.sleep(0.4)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/jobs/{jid}/zip")
def zip_all(jid: str):
    d = OUT / jid
    if not d.exists(): raise HTTPException(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(d.glob("clip_*.mp4")) + sorted(d.glob("clip_*.srt")):
            z.write(f, f.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename=offcut-{jid}.zip"})



class RecapReq(BaseModel):
    source: str
    length: int = 1          # 1=60s Shorts, or 5/8/12 minutes
    fmt: str = "9:16"        # 9:16 or 16:9
    voice: str = "en-US-AndrewNeural"
    spoilers: bool = False
    captions: bool = True


@app.get("/api/recap/options")
def recap_options():
    return {
        "voices": VOICES,
        "lengths": [
            {"id": 1, "label": "Shorts 60s"},
            {"id": 5, "label": "5 min"},
            {"id": 8, "label": "8 min"},
            {"id": 12, "label": "12 min"},
        ],
        "formats": ["9:16", "16:9"],
    }


@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (file.filename or "video.mp4"))
    dest = UPLOADS / f"{uuid_hex()}_{safe}"
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return {"path": str(dest), "name": file.filename, "size": dest.stat().st_size}


@app.post("/api/recap")
def recap(req: RecapReq):
    job = RecapJob(
        req.source.strip(),
        length=req.length,
        fmt=req.fmt,
        voice=req.voice,
        spoilers=req.spoilers,
        captions_on=req.captions,
    )
    JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id}


# ── Channel watch ──────────────────────────────────────────────

@app.get("/api/watch")
def watch_status():
    return watch.status()


@app.post("/api/watch")
def watch_add(req: WatchAdd):
    try:
        ch = watch.add_channel(req.url, auto_upload=req.auto_upload)
        return {"ok": True, "channel": ch}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/watch/{channel_id}")
def watch_del(channel_id: str):
    watch.remove_channel(channel_id)
    return {"ok": True}


@app.patch("/api/watch/{channel_id}")
def watch_patch(channel_id: str, req: WatchToggle):
    try:
        watch.set_auto_upload(channel_id, req.auto_upload)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.put("/api/watch/poll")
def watch_poll(req: PollMins):
    watch.set_poll_minutes(req.minutes)
    return {"ok": True, "poll_minutes": watch.load()["poll_minutes"]}


@app.post("/api/watch/poll-now")
def watch_poll_now():
    started = watch.poll_once(_register)
    return {"ok": True, "started": started}


# ── YouTube upload ─────────────────────────────────────────────

@app.get("/api/youtube")
def youtube_status():
    return yt.status()


@app.post("/api/youtube/connect")
def youtube_connect():
    try:
        return yt.start_oauth()
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    yt.disconnect()
    return {"ok": True}


@app.post("/api/upload")
def upload(req: UploadReq):
    job = JOBS.get(req.job_id)
    clip_meta = None
    if job:
        clip_meta = next((c for c in job.clips if c["name"] == req.clip), None)
        mp4 = job.dir / f"{req.clip}.mp4"
    else:
        # fall back to on-disk job.json for finished jobs after restart
        d = OUT / req.job_id
        mp4 = d / f"{req.clip}.mp4"
        jj = d / "job.json"
        if jj.exists():
            data = json.loads(jj.read_text())
            clip_meta = next((c for c in data.get("clips", []) if c["name"] == req.clip), None)
    if not mp4.exists():
        raise HTTPException(404, "clip not found")
    if not clip_meta:
        clip_meta = {"name": req.clip, "title": req.clip, "description": "", "hashtags": []}
    try:
        result = yt.upload_clip(mp4, clip_meta, privacy=req.privacy)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(400, str(e))


# ── Documentary mode ───────────────────────────────────────────
class DocuReq(BaseModel):
    topic: str
    minutes: float = 10
    script: dict | None = None


class DocuUp(BaseModel):
    name: str
    privacy: str = "private"


@app.post("/api/docu")
def docu_start(req: DocuReq):
    if not req.topic.strip():
        raise HTTPException(400, "topic required")
    job = docu.DocuJob(req.topic, minutes=max(1, min(15, req.minutes)), script=req.script)
    JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id}


@app.get("/api/docu/{jid}")
def docu_state(jid: str):
    job = JOBS.get(jid)
    if job: return job.state()
    jj = OUT / jid / "job.json"
    if jj.exists(): return json.loads(jj.read_text())
    raise HTTPException(404)


@app.post("/api/docu/{jid}/upload")
def docu_upload(jid: str, req: DocuUp):
    d = OUT / jid
    if not (d / "job.json").exists(): raise HTTPException(404, "job not finished")
    try:
        return {"ok": True, **docu.upload(d, req.name, req.privacy)}
    except Exception as e:
        raise HTTPException(400, str(e))


app.mount("/files", StaticFiles(directory=OUT), name="files")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")

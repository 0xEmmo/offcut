import asyncio, json, threading, zipfile, io
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .pipeline import Job, OUT
from . import brain

app = FastAPI(title="offcut")
JOBS: dict[str, Job] = {}
WEB = Path(__file__).resolve().parent.parent / "web"
OUT.mkdir(exist_ok=True)


class CutReq(BaseModel):
    source: str
    model: str = "auto"
    captions: bool = True
    count: int = 4


@app.get("/api/brains")
def brains():
    return {"default": brain.available()}


@app.post("/api/cut")
def cut(req: CutReq):
    job = Job(req.source.strip(), req.model, req.captions, max(1, min(req.count, 8)))
    JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id}


@app.get("/api/jobs/{jid}/events")
async def events(jid: str):
    job = JOBS.get(jid) or HTTPException(404)
    if isinstance(job, HTTPException): raise job
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


app.mount("/files", StaticFiles(directory=OUT), name="files")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")

import json, subprocess, time, uuid
from pathlib import Path
from . import ingest, transcribe, brain, reframe, captions

OUT = Path(__file__).resolve().parent.parent / "out"
STEPS = ["INGEST", "TRANSCRIPT", "SCENE", "DIRECT", "RENDER"]


class Job:
    def __init__(self, source, model="auto", captions=True, n=4):
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.source, self.model, self.captions, self.n = source, model, captions, n
        self.dir = OUT / self.id
        self.progress = {s: 0.0 for s in STEPS}
        self.status, self.error, self.clips, self.log = "running", None, [], []
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _check(self):
        if self._cancel:
            raise RuntimeError("cancelled")

    def set(self, step, p, msg=None):
        self.progress[step] = round(p, 3)
        if msg: self.log.append(f"{step.lower()}: {msg}")

    def state(self):
        return {"id": self.id, "status": self.status, "error": self.error,
                "progress": self.progress, "log": self.log[-6:], "clips": self.clips}

    def run(self):
        try:
            self._run(); self.status = "done"
        except Exception as e:
            if self._cancel or str(e) == "cancelled":
                self.status, self.error = "cancelled", "cancelled"
            else:
                self.status, self.error = "error", str(e)
        (self.dir / "job.json").write_text(json.dumps(self.state(), indent=2)) if self.dir.exists() else None

    def _run(self):
        self.set("INGEST", 0.1, f"fetching {self.source}")
        src = ingest.ingest(self.source, self.dir)
        meta = ingest.probe(src)
        self.set("INGEST", 1, f"{meta['w']}x{meta['h']}, {meta['duration']:.0f}s")
        self._check()

        def _prog(p):
            self._check(); self.set("TRANSCRIPT", p)
        words = transcribe.transcribe(src, _prog)
        (self.dir / "words.json").write_text(json.dumps(words))
        self.set("TRANSCRIPT", 1, f"{len(words)} words")
        self._check()

        self.set("SCENE", 0.3, "scoring windows")
        used, picks = brain.pick(words, meta["duration"], self.model, self.n)
        self.set("SCENE", 1, f"{len(picks)} moments via {used}")
        self._check()

        crop_w = min(meta["w"], int(meta["h"] * 9 / 16)) // 2 * 2
        tracks = []
        for i, c in enumerate(picks):
            self._check()
            tr = reframe.face_track(src, float(c["start"]), float(c["end"]), meta["w"], meta["h"])
            tracks.append(tr)
            self.set("DIRECT", (i + 1) / len(picks), f"clip {i+1}: {'face track' if tr else 'centre crop'}")

        for i, (c, tr) in enumerate(zip(picks, tracks)):
            self._check()
            s, e = float(c["start"]), float(c["end"])
            cw = [{"w": w["w"], "s": w["s"] - s, "e": w["e"] - s} for w in words if s <= w["s"] < e]
            name = f"clip_{i+1:02d}"
            (self.dir / f"{name}.srt").write_text(captions.build_srt(cw))
            vf = f"crop={crop_w}:{meta['h']}:'{reframe.crop_expr(tr, meta['w'], crop_w)}':0,scale=1080:1920,setsar=1"
            if self.captions:
                (self.dir / f"{name}.ass").write_text(captions.build_ass(cw))
                vf += f",ass={name}.ass"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s}", "-to", f"{e}", "-i", src.name,
                            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", f"{name}.mp4"],
                           cwd=self.dir, check=True, capture_output=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", f"{name}.mp4", "-frames:v", "1",
                            "-vf", "scale=270:-2", f"{name}.jpg"], cwd=self.dir, check=True)
            subs = {k: int(round(float(c.get(k, 5)))) for k in ("hook", "shock", "comedy", "summary", "silence")}
            score = round(sum(subs.values()) / 5 * 10)
            self.clips.append({"name": name, "start": s, "end": e, "duration": round(e - s, 1),
                               "score": score, "sub": subs, "why": c.get("why", ""),
                               "title": c.get("title", ""), "description": c.get("description", ""),
                               "hashtags": c.get("hashtags", []), "brain": used,
                               "mp4": f"/files/{self.id}/{name}.mp4", "srt": f"/files/{self.id}/{name}.srt",
                               "thumb": f"/files/{self.id}/{name}.jpg"})
            self.set("RENDER", (i + 1) / len(picks), f"{name}.mp4 rendered")

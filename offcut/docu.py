"""DOCUMENTARY mode: topic -> script (1 Gemini call) -> Wikimedia Commons stills ->
edge-tts voiceover -> Ken Burns + burned captions -> long 16:9 + 2 Shorts 9:16, thumbnails, metadata.
Copyright-clean by design: only public-domain / CC0 / CC BY(-SA) images from Commons, credited."""
import asyncio, json, os, random, re, subprocess, time, uuid, urllib.parse
from pathlib import Path
import httpx
from .pipeline import OUT

VOICE = "en-US-AndrewNeural"
FONT = os.getenv("OFFCUT_FONT", "/usr/share/fonts/truetype/sand-box/google/Anton/Anton-Regular.ttf")
UA = {"User-Agent": "offcut-docu/0.4 (https://github.com/0xEmmo/offcut)"}
OKLIC = ("public domain", "cc0", "cc by", "pd")
STEPS = ["SCRIPT", "IMAGES", "LONG", "SHORT 1", "SHORT 2", "PACKAGE"]
CH = "\n\nTrue stories, unsolved mysteries and history. Subscribe for new documentaries every week."

PROMPT = """You write scripts for a faceless YouTube documentary channel (mysteries, true crime, history).
Topic: {topic}
Write a factual, gripping, non-graphic documentary narration. Stick to well documented facts; say "reportedly" when unsure.
Return ONLY JSON with this shape:
{{"title": "<=90 char YouTube title", "description": "2-3 sentence description",
 "tags": ["10-14 search tags"], "hashtags": ["#Tag", "... 5 total"], "thumb": ["2-3 WORD LINE", "2-4 WORD LINE"],
 "image_queries": ["10 BROAD 2-4 word Wikimedia Commons search queries for real photos, documents, maps, places and people of this topic, e.g. the key person name, the place name, the object type (no movies/TV)"],
 "long": [{{"q": <index into image_queries>, "text": "2-3 narration sentences"}}],
 "shorts": [{{"title": "<=70 chars", "description": "one line", "hook": "2-5 WORD ON-SCREEN HOOK", "thumb": ["LINE1", "LINE2"],
   "segments": [{{"q": 0, "text": "..."}}]}}, {{...second short, different angle...}}]}}
Rules: "long" totals about {words} words in {segs} segments; the FIRST segment is a hook that makes viewers stay; the last asks viewers to comment and subscribe.
Each short is 4-5 segments, 70-110 words total, starts with a hook, ends with "Follow for more."
Write numbers and years as words or digits that read naturally aloud. No markdown."""


class QuotaError(RuntimeError):
    pass


def ask_gemini(topic: str, minutes: float) -> dict:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("No GEMINI_API_KEY in .env. Add one or paste your own script JSON.")
    words = int(minutes * 150)
    m = os.getenv("OFFCUT_GEMINI_MODEL", "gemini-flash-latest")
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                   params={"key": key}, timeout=300,
                   json={"contents": [{"parts": [{"text": PROMPT.format(topic=topic, words=words, segs=max(6, words // 45))}]}],
                         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}})
    if r.status_code == 429 or "RESOURCE_EXHAUSTED" in r.text[:500]:
        raise QuotaError("Gemini quota exceeded for now. Wait (free quota resets daily) or paste your own script JSON.")
    r.raise_for_status()
    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    txt = re.sub(r"^```(json)?|```$", "", txt.strip(), flags=re.M)
    return json.loads(txt)


# ── Wikimedia Commons ──────────────────────────────────────────
def commons(q: str, n: int, dest: Path, prefix: str, seen: set) -> list:
    r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=60, params=dict(
        action="query", format="json", generator="search", gsrsearch=q + " filetype:bitmap", gsrnamespace=6,
        gsrlimit=12, prop="imageinfo", iiprop="url|extmetadata|size", iiurlwidth=2400)).json()
    out = []
    for pg in sorted(r.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        if len(out) >= n: break
        ii = pg["imageinfo"][0]; m = ii.get("extmetadata", {})
        lic = m.get("LicenseShortName", {}).get("value", "")
        if pg["title"] in seen or not lic.lower().startswith(OKLIC) or "nc" in lic.lower() or "nd" in lic.lower().split("-"):
            continue
        if ii.get("width", 0) < 400 or re.search(r"\.(pdf|djvu|svg|webm|ogv)$", pg["title"], re.I):
            continue
        k = f"{prefix}{chr(97 + len(out))}"
        try:
            raw = httpx.get(ii.get("thumburl", ii["url"]), headers=UA, timeout=90, follow_redirects=True).content
            tmp = dest / "_dl"; tmp.write_bytes(raw)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-frames:v", "1", "-vf",
                            "scale='min(2400,iw)':-2", str(dest / f"{k}.png")], check=True, timeout=120)
        except Exception:
            continue
        art = re.sub("<[^>]+>", "", m.get("Artist", {}).get("value", "")).strip()[:100]
        seen.add(pg["title"])
        out.append({"key": k, "title": pg["title"][5:], "license": lic, "author": art or "Unknown", "url": ii["descriptionurl"]})
    return out


# ── render ─────────────────────────────────────────────────────
async def _tts(text, mp3):
    import edge_tts
    c = edge_tts.Communicate(text, VOICE, rate="+4%", boundary="WordBoundary"); wb = []
    with open(mp3, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio": f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                wb.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    return wb


def _sh(*a):
    p = subprocess.run(a, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if p.returncode: raise RuntimeError(f"ffmpeg failed: {p.stderr[-400:]}")


def _dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))


def _ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def render(segs, imgdir: Path, out: Path, vert: bool, hook: str, progress=lambda f: None, check=lambda: None):
    W, H = (1080, 1920) if vert else (1920, 1080)
    tmp = out.with_suffix(".tmp"); tmp.mkdir(exist_ok=True)
    caps, t0, vl, al = [], 0.0, [], []
    for i, (img, text) in enumerate(segs):
        check()
        mp3 = tmp / f"{i}.mp3"
        for _ in range(6):
            try: wb = asyncio.run(asyncio.wait_for(_tts(text, mp3), 90)); break
            except Exception as e: err = e
        else: raise RuntimeError(f"voiceover failed: {err}")
        d = _dur(mp3) + 0.35; n = 3 if vert else 5
        for j in range(0, len(wb), n):
            g = wb[j:j + n]
            caps.append((t0 + g[0][0], t0 + (wb[j + n][0] if j + n < len(wb) else g[-1][1] + 0.2), " ".join(x[2] for x in g)))
        mv = tmp / f"{i}.mp4"
        if not mv.exists():
            Z = 1.12; dx, dy = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)])
            x = f"(iw-{W})/2+{dx}*(iw-{W})/2*(2*t/{d:.2f}-1)"; y = f"(ih-{H})/2+{dy}*(ih-{H})/2*(2*t/{d:.2f}-1)"
            fc = (f"[0]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30,eq=brightness=-0.15[bg];"
                  f"[b]scale={W}:{int(H * (0.62 if vert else 0.94))}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                  f"scale={int(W * Z)}:{int(H * Z)},crop={W}:{H}:'{x}':'{y}',fps=30,format=yuv420p")
            _sh("ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(img), "-t", f"{d:.3f}", "-filter_complex", fc,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", str(mv) + ".part.mp4")
            os.replace(str(mv) + ".part.mp4", mv)
        _sh("ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", f"apad=whole_dur={d:.3f}", "-ar", "44100", "-ac", "2", str(tmp / f"{i}.wav"))
        vl.append(mv); al.append(tmp / f"{i}.wav"); t0 += d
        progress((i + 1) / (len(segs) + 2))
    (tmp / "v.txt").write_text("".join(f"file '{p}'\n" for p in vl))
    (tmp / "a.txt").write_text("".join(f"file '{p}'\n" for p in al))
    fs, mv_ = (78, 520) if vert else (64, 70)
    ass = (f"[Script Info]\nPlayResX: {W}\nPlayResY: {H}\nScriptType: v4.00+\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           f"Style: C,Anton,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,5,2,2,60,60,{mv_},1\n"
           f"Style: Hook,Anton,{110 if vert else 120},&H0000D6FF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,7,3,8,60,60,{260 if vert else 90},1\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    if hook: ass += f"Dialogue: 1,{_ts(0)},{_ts(3.2)},Hook,,0,0,0,,{{\\fad(150,300)}}{hook.upper()}\n"
    for a, b, t in caps: ass += f"Dialogue: 0,{_ts(a)},{_ts(b)},C,,0,0,0,,{t.upper()}\n"
    (tmp / "c.ass").write_text(ass)
    _sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "v.txt"), "-f", "concat", "-safe", "0",
        "-i", str(tmp / "a.txt"), "-vf", f"ass={tmp / 'c.ass'}:fontsdir={Path(FONT).parent}", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out))
    progress(1.0)
    return t0


def thumb(img, l1, l2, out, W=1280, H=720):
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    im = Image.open(img).convert("RGB"); r = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1))
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2, (im.width - W) // 2 + W, (im.height - H) // 2 + H))
    im = ImageEnhance.Contrast(ImageEnhance.Brightness(im).enhance(0.6)).enhance(1.3)
    g = Image.new("L", (W, H)); gd = ImageDraw.Draw(g)
    for x in range(W): gd.line([(x, 0), (x, H)], fill=int(200 * max(0, 1 - x / (W * 0.75))))
    im.paste(Image.new("RGB", (W, H)), (0, 0), g); d = ImageDraw.Draw(im)
    def fit(t, s):
        while ImageFont.truetype(FONT, s).getlength(t) > W * 0.62 and s > 30: s -= 4
        return ImageFont.truetype(FONT, s)
    f1, f2 = fit(l1, 170), fit(l2, 110); y = H * 0.18
    d.text((50, y), l1, font=f1, fill=(255, 214, 0), stroke_width=6, stroke_fill=(0, 0, 0)); y += f1.size * 1.15
    d.text((50, y), l2, font=f2, fill="white", stroke_width=5, stroke_fill=(0, 0, 0))
    d.rectangle([50, y + f2.size * 1.3, 330, y + f2.size * 1.3 + 14], fill=(229, 9, 20))
    im.save(out, quality=90)


class DocuJob:
    def __init__(self, topic: str, minutes: float = 10, script: dict | None = None):
        self.id = "docu-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.topic, self.minutes, self.script = topic.strip(), minutes, script
        self.dir = OUT / self.id
        self.progress = {s: 0.0 for s in STEPS}
        self.status, self.error, self.log, self.videos = "running", None, [], []
        self._cancel = False

    def cancel(self): self._cancel = True
    def _check(self):
        if self._cancel: raise RuntimeError("cancelled")
    def set(self, step, p, msg=None):
        self.progress[step] = round(p, 3)
        if msg: self.log.append(f"{step.lower()}: {msg}")
    def state(self):
        return {"id": self.id, "kind": "docu", "topic": self.topic, "status": self.status, "error": self.error,
                "progress": self.progress, "log": self.log[-8:], "videos": self.videos}

    def run(self):
        try:
            self._run(); self.status = "done"
        except Exception as e:
            self.status, self.error = ("cancelled", "cancelled") if self._cancel else ("error", str(e))
        if self.dir.exists(): (self.dir / "job.json").write_text(json.dumps(self.state(), indent=2))

    def _run(self):
        self.dir.mkdir(parents=True, exist_ok=True); img = self.dir / "img"; img.mkdir(exist_ok=True)
        self.set("SCRIPT", 0.2, "writing script (1 Gemini call)" if not self.script else "using pasted script")
        S = self.script or ask_gemini(self.topic, self.minutes)
        (self.dir / "script.json").write_text(json.dumps(S, indent=2))
        self.set("SCRIPT", 1, f"{sum(len(s['text'].split()) for s in S['long'])} words, {len(S['long'])} segments")
        qs = S.get("image_queries") or [self.topic]
        credits, seen, pool = [], set(), {}
        for i, q in enumerate(qs):
            self._check()
            got, words = [], q.split()
            while not got and words:  # broaden the query until Commons returns something usable
                got = commons(" ".join(words), 3, img, f"q{i}", seen); words = words[:-1] if len(words) > 2 else []
            credits += got; pool[i] = [g["key"] for g in got]
            self.set("IMAGES", (i + 1) / len(qs), f"'{q}': {len(got)} images")
        if len(credits) < 12:  # top up with whole-topic searches
            for j, q in enumerate([self.topic, " ".join(self.topic.split()[:3]), S.get("title", "")[:40]]):
                if len(credits) >= 12 or not q: break
                got = commons(q, 12 - len(credits), img, f"t{j}", seen); credits += got
            for i in pool:
                if not pool[i]: pool[i] = [c["key"] for c in credits]
        if not credits: raise RuntimeError("No public-domain / CC images found on Wikimedia Commons for this topic.")
        allk = [c["key"] for c in credits]; used = set(); rot = {}
        def pick(q):
            ks = pool.get(q) or allk; k = ks[rot.get(q, 0) % len(ks)]; rot[q] = rot.get(q, 0) + 1; used.add(k); return k
        (self.dir / "credits.json").write_text(json.dumps(credits, indent=2))
        jobs = [("long", "LONG", S["long"], False, S.get("thumb", ["", ""])[0], S)]
        for n, sh in enumerate(S.get("shorts", [])[:2]):
            jobs.append((f"short{n + 1}", f"SHORT {n + 1}", sh["segments"], True, sh.get("hook", ""), sh))
        for name, step, segs, vert, hook, meta in jobs:
            keys = [pick(int(s.get("q", 0)) if str(s.get("q", 0)).lstrip("-").isdigit() else 0) for s in segs]
            pairs = [(img / f"{k}.png", s["text"]) for k, s in zip(keys, segs)]
            self.set(step, 0.01, "rendering")
            out = self.dir / f"{name}.mp4"
            dur = render(pairs, img, out, vert, hook, lambda f, st=step: self.set(st, f), self._check)
            ukeys = set(keys); cr = [c for c in credits if c["key"] in ukeys]
            th = meta.get("thumb") or S.get("thumb") or [self.topic[:20].upper(), ""]
            thumb(img / f"{keys[0]}.png", th[0].upper(), (th[1] if len(th) > 1 else "").upper(), self.dir / f"{name}.jpg")
            tags = S.get("tags", []) + (["Shorts"] if vert else [])
            ht = S.get("hashtags", [])[:5]; ht = (["#Shorts"] + ht[:3]) if vert else ht
            title = (meta.get("title") or S["title"]).strip()
            if vert and "#shorts" not in title.lower(): title = f"{title} #Shorts"
            desc = (meta.get("description") or S.get("description", "")) + "\n\n" + " ".join(ht) + CH
            desc += "\n\nSOURCES & IMAGE CREDITS (via Wikimedia Commons):\n" + "".join(
                f"- {c['title']} | {c['author'][:60]} | {c['license']} | {c['url']}\n" for c in cr)
            desc += "Narration: AI voice. No film or TV footage used."
            v = {"name": name, "title": title[:100], "description": desc[:4900], "tags": tags[:30], "vertical": vert,
                 "duration": round(dur, 1), "mp4": f"/files/{self.id}/{name}.mp4", "thumb": f"/files/{self.id}/{name}.jpg"}
            (self.dir / f"{name}.txt").write_text(f"TITLE:\n{v['title']}\n\nDESCRIPTION:\n{v['description']}\n\nTAGS:\n{', '.join(v['tags'])}\n")
            self.videos.append(v); self.set(step, 1, f"{name}: {dur / 60:.1f} min")
        self.set("PACKAGE", 1, "done: videos, thumbnails, metadata ready")


def upload(jobdir: Path, name: str, privacy: str = "private") -> dict:
    from . import youtube_upload as yu
    Credentials, InstalledAppFlow, Request, build, MediaFileUpload = yu._import_google()
    creds = yu._creds()
    if not creds: raise RuntimeError("YouTube not connected. Click Connect YouTube first.")
    v = next(x for x in json.loads((jobdir / "job.json").read_text())["videos"] if x["name"] == name)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {"snippet": {"title": v["title"], "description": v["description"], "tags": v["tags"], "categoryId": "27"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(jobdir / f"{name}.mp4"), mimetype="video/mp4", resumable=True, chunksize=8 << 20))
    resp = None
    while resp is None: _, resp = req.next_chunk()
    vid = resp["id"]; th = "set"
    try: yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(jobdir / f"{name}.jpg"), mimetype="image/jpeg")).execute()
    except Exception as e: th = "not set (" + str(e)[:140] + "). Add it in YouTube Studio."
    url = ("https://www.youtube.com/shorts/" if v["vertical"] else "https://www.youtube.com/watch?v=") + vid
    return {"id": vid, "url": url, "privacy": privacy, "thumbnail": th}

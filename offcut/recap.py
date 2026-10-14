"""Movie recap mode: commentary voiceover + short supporting clips."""
from __future__ import annotations

import asyncio, json, os, re, subprocess, time, uuid
from pathlib import Path

from . import ingest, transcribe, brain, captions

OUT = Path(__file__).resolve().parent.parent / "out"
UPLOADS = Path(__file__).resolve().parent.parent / "data" / "uploads"
STEPS = ["INGEST", "TRANSCRIPT", "SCENE", "SCRIPT", "VOICE", "RENDER"]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

VOICES = [
    {"id": "en-US-AndrewNeural", "label": "Andrew (US)"},
    {"id": "en-US-AvaNeural", "label": "Ava (US)"},
    {"id": "en-US-BrianNeural", "label": "Brian (US)"},
    {"id": "en-GB-RyanNeural", "label": "Ryan (UK)"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia (UK)"},
]

LENGTH_TARGETS = {5: 5 * 60, 8: 8 * 60, 12: 12 * 60, 1: 60}  # 1 = Shorts 60s


SCRIPT_PROMPT = """You write original film commentary / review-style recaps for YouTube.
NOT a beat-by-beat plot dump. Speak in the narrator's own voice with analysis, opinion,
craft notes and personality. Short supporting clips will illustrate each line.

Film source: {title}
Duration: {duration:.0f} seconds
Scene cut timestamps (seconds): {scenes}
Transcript (may be empty for silent films):
{transcript}

Target spoken length: about {target_sec} seconds (~{words} words).
Format: {fmt}
Spoiler policy: {spoiler_policy}

Return JSON ONLY (no markdown):
{{
  "title": "YouTube title under 90 chars",
  "description": "2-4 sentence description + disclaimer that this is commentary/review",
  "hashtags": ["#MovieRecap", "#FilmReview", "..."],
  "chapters": [{{"label": "short chapter label", "after_line": 0}}],
  "lines": [
    {{
      "text": "one spoken sentence or short beat",
      "clip_start": 12.4,
      "clip_end": 16.8,
      "chapter": "optional chapter label or null"
    }}
  ]
}}

Rules:
- clip_end - clip_start must be between 2 and 6 seconds
- clip timestamps must fall inside 0..{duration:.0f}
- prefer scene-cut neighbourhoods when picking clips
- do NOT reveal the ending unless spoiler policy says allowed
- lines should flow as continuous narration
- enough lines to fill the target spoken length
"""


def _esc_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "%%")


def detect_scenes(src: Path, threshold: float = 0.32) -> list[float]:
    """Return scene-change timestamps via ffmpeg select+showinfo."""
    cmd = [
        "ffmpeg", "-v", "info", "-i", str(src),
        "-vf", f"select=gt(scene\\,{threshold}),showinfo",
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    times = []
    for m in re.finditer(r"pts_time:(\d+(?:\.\d+)?)", r.stderr or ""):
        times.append(float(m.group(1)))
    # always include start; dedupe + cap
    out = [0.0]
    for t in times:
        if t - out[-1] >= 1.5:
            out.append(t)
    return out[:80]


def _transcript_for_prompt(words, max_chars=6000) -> str:
    if not words:
        return "(no dialogue / silent or music-only)"
    text = brain._transcript_text(words)
    return text[:max_chars]


def ask_script(title, duration, scenes, words, target_sec, fmt, spoilers) -> dict:
    b = brain.available()
    if b == "heuristic":
        return _heuristic_script(title, duration, scenes, target_sec)
    spoiler_policy = "ENDING ALLOWED — you may discuss the ending" if spoilers else \
        "NO ENDING — stop before the climax resolution; tease without spoiling"
    words_n = max(80, int(target_sec * 2.4))
    scene_str = ", ".join(f"{t:.1f}" for t in scenes[:40]) or "0.0"
    prompt = SCRIPT_PROMPT.format(
        title=title, duration=duration, scenes=scene_str,
        transcript=_transcript_for_prompt(words),
        target_sec=target_sec, words=words_n, fmt=fmt,
        spoiler_policy=spoiler_policy,
    )
    try:
        raw = brain._ask(b, prompt)
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        lines = []
        for ln in data.get("lines") or []:
            s, e = float(ln["clip_start"]), float(ln["clip_end"])
            if e <= s:
                e = s + 3
            e = min(max(e, s + 2), s + 6, duration)
            s = max(0.0, min(s, duration - 2))
            if e <= s:
                continue
            lines.append({
                "text": str(ln.get("text", "")).strip(),
                "clip_start": s, "clip_end": e,
                "chapter": ln.get("chapter"),
            })
        if not lines:
            return _heuristic_script(title, duration, scenes, target_sec)
        data["lines"] = lines[:24]
        data.setdefault("title", f"{title} — Recap")
        data.setdefault("description", f"Commentary recap of {title}. Fair-use review/commentary.")
        data.setdefault("hashtags", ["#MovieRecap", "#FilmReview", "#Shorts"])
        data["_brain"] = b
        return data
    except Exception as e:
        print(f"[recap] script brain failed ({e}); heuristic")
        return _heuristic_script(title, duration, scenes, target_sec)


def _heuristic_script(title, duration, scenes, target_sec):
    """Fallback without LLM: generic commentary beats at scene cuts."""
    n = max(6, min(18, int(target_sec / 5)))
    pts = scenes[:] if scenes else [i * max(duration / n, 3) for i in range(n)]
    while len(pts) < n:
        pts.append(min(duration * len(pts) / n, max(duration - 4, 0)))
    templates = [
        f"Let's talk about {title} — not a beat-for-beat dump, just what actually works.",
        "The opening already tells you the tone: economical, visual, and a little mean.",
        "Watch how the framing does the joking before anyone has to explain it.",
        "This is the kind of bit modern comedies still steal and sand down.",
        "I'm less interested in the plot than in the craft choices landing on screen.",
        "Small props, big timing — that's the whole thesis in one shot.",
        "If you're here for spoilers, pause. This is commentary, not a spoiler reel.",
        "The middle stretch is where the film stops being cute and starts being precise.",
        "That's the cut I'd show a film student: setup, punch, hold.",
        "You can feel the audience of the time discovering the gag in real time.",
        "And that's why this still travels — the joke is visual, not dated dialogue.",
        "Quick recap energy, long-term craft lesson. That's the point of watching it this way.",
    ]
    lines = []
    for i in range(n):
        s = float(pts[i % len(pts)])
        s = max(0.0, min(s, max(duration - 3.5, 0)))
        e = min(s + 3.5, duration)
        lines.append({
            "text": templates[i % len(templates)],
            "clip_start": s, "clip_end": e,
            "chapter": "Opening" if i == 0 else ("Craft" if i == n // 2 else None),
        })
    return {
        "title": f"{title} — Quick Commentary Recap",
        "description": f"Original commentary-style look at {title}. Short clips for illustration; not a full plot dump.",
        "hashtags": ["#MovieRecap", "#FilmReview", "#SilentFilm", "#Shorts"],
        "chapters": [],
        "lines": lines,
        "_brain": "heuristic",
    }


async def _tts(text: str, voice: str, mp3: Path) -> list[dict]:
    """Synthesize narration. Word timings come later via whisper on the audio."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    audio = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    mp3.write_bytes(audio)
    return []


def _card(path: Path, text: str, w: int, h: int, dur: float = 2.0, sub: str = ""):
    lines = []
    t = _esc_drawtext(text[:48])
    lines.append(
        f"drawtext=fontfile={FONT}:text='{t}':fontsize={42 if h > w else 48}:"
        f"fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-20"
    )
    if sub:
        s = _esc_drawtext(sub[:60])
        lines.append(
            f"drawtext=fontfile={FONT}:text='{s}':fontsize=28:"
            f"fontcolor=0xFFD23F:x=(w-text_w)/2:y=(h-text_h)/2+40"
        )
    vf = ",".join(lines)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=0x0a0a0b:s={w}x{h}:d={dur}:r=30",
         "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(dur), str(path)],
        check=True, capture_output=True,
    )


def _cut_clip(src: Path, start: float, end: float, out: Path, tw: int, th: int, mute=True):
    dur = max(0.5, end - start)
    # scale+crop to fill target
    vf = (
        f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(src), "-vf", vf, "-an" if mute else "-c:a", "aac",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", "30", str(out),
    ]
    # fix cmd when mute - the ternary above breaks the list. rebuild:
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(src), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", "30",
    ]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd.append(str(out))
    subprocess.run(cmd, check=True, capture_output=True)


def _probe_dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def _fit_clip_to_dur(clip: Path, need: float, out: Path, tw: int, th: int):
    """Pad with freeze or trim so video segment matches narration beat length."""
    have = _probe_dur(clip)
    if abs(have - need) < 0.05:
        if clip.resolve() != out.resolve():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-c", "copy", str(out)],
                           check=True, capture_output=True)
        return
    if have >= need:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-t", f"{need:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an",
             "-pix_fmt", "yuv420p", str(out)],
            check=True, capture_output=True)
    else:
        pad = min(need - have, 8.0)
        need = have + pad
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(clip),
             "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an",
             "-pix_fmt", "yuv420p", "-t", f"{need:.3f}", str(out)],
            check=True, capture_output=True)


class RecapJob:
    def __init__(self, source: str, *, length: int = 1, fmt: str = "9:16",
                 voice: str = "en-US-AndrewNeural", spoilers: bool = False,
                 captions_on: bool = True, duck: float = 0.08):
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.source = source
        self.length = length if length in LENGTH_TARGETS else 1
        self.fmt = fmt if fmt in ("9:16", "16:9") else "9:16"
        self.voice = voice
        self.spoilers = spoilers
        self.captions_on = captions_on
        self.duck = max(0.0, min(duck, 0.4))
        self.dir = OUT / self.id
        self.progress = {s: 0.0 for s in STEPS}
        self.status, self.error, self.clips, self.log = "running", None, [], []
        self._cancel = False
        self.mode = "recap"
        self.steps = list(STEPS)

    def cancel(self):
        self._cancel = True

    def _check(self):
        if self._cancel:
            raise RuntimeError("cancelled")

    def set(self, step, p, msg=None):
        self.progress[step] = round(p, 3)
        if msg:
            self.log.append(f"{step.lower()}: {msg}")

    def state(self):
        return {
            "id": self.id, "status": self.status, "error": self.error,
            "progress": self.progress, "log": self.log[-8:], "clips": self.clips,
            "mode": self.mode, "steps": self.steps, "fmt": self.fmt,
        }

    def run(self):
        try:
            self._run()
            self.status = "done"
        except Exception as e:
            if self._cancel or str(e) == "cancelled":
                self.status, self.error = "cancelled", "cancelled"
            else:
                self.status, self.error = "error", str(e)
        if self.dir.exists():
            (self.dir / "job.json").write_text(json.dumps(self.state(), indent=2))

    def _run(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tw, th = (1080, 1920) if self.fmt == "9:16" else (1920, 1080)
        target_sec = LENGTH_TARGETS[self.length]

        self.set("INGEST", 0.1, f"loading {self.source}")
        src = ingest.ingest(self.source, self.dir)
        meta = ingest.probe(src)
        title = Path(self.source).stem.replace("_", " ").replace("-", " ")
        title = re.sub(r"\s+", " ", title).strip()[:80] or "Film"
        self.set("INGEST", 1, f"{meta['w']}x{meta['h']}, {meta['duration']:.0f}s · {title}")
        self._check()

        def _prog(p):
            self._check()
            self.set("TRANSCRIPT", p)

        words = []
        has_audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True).stdout.strip()
        if not has_audio:
            self.set("TRANSCRIPT", 1, "no audio stream (silent) — skip whisper")
        else:
            try:
                words = transcribe.transcribe(src, _prog)
            except Exception as e:
                self.set("TRANSCRIPT", 0.5, f"transcript skipped: {e}")
        (self.dir / "words.json").write_text(json.dumps(words))
        self.set("TRANSCRIPT", 1, f"{len(words)} words")
        self._check()

        self.set("SCENE", 0.2, "detecting cuts")
        scenes = detect_scenes(src)
        (self.dir / "scenes.json").write_text(json.dumps(scenes))
        self.set("SCENE", 1, f"{len(scenes)} scene points")
        self._check()

        self.set("SCRIPT", 0.2, "writing commentary")
        script = ask_script(title, meta["duration"], scenes, words, target_sec, self.fmt, self.spoilers)
        (self.dir / "script.json").write_text(json.dumps(script, indent=2))
        self.set("SCRIPT", 1, f"{len(script['lines'])} lines via {script.get('_brain', '?')}")
        self._check()

        # Voiceover
        self.set("VOICE", 0.1, f"tts {self.voice}")
        narr_text = " ".join(ln["text"] for ln in script["lines"] if ln.get("text"))
        narr_text = re.sub(r"\s+", " ", narr_text).strip()
        # hard cap spoken length so TTS/render can't runaway
        max_words = 160 if self.length == 1 else int(target_sec * 2.6)
        parts = narr_text.split()
        if len(parts) > max_words:
            narr_text = " ".join(parts[:max_words])
            self.log.append(f"voice: truncated narration to {max_words} words")
        mp3 = self.dir / "narration.mp3"
        vo_words = asyncio.run(_tts(narr_text, self.voice, mp3))
        # convert to wav/aac-friendly
        vo_wav = self.dir / "narration.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "44100", "-ac", "2", str(vo_wav)],
            check=True, capture_output=True)
        vo_dur = _probe_dur(vo_wav)
        # edge-tts often omits WordBoundary now — align captions with whisper on the VO
        if not vo_words:
            try:
                vo_words = transcribe.transcribe(vo_wav, lambda p: self.set("VOICE", 0.4 + 0.5 * p))
            except Exception as e:
                self.log.append(f"voice: caption align failed ({e})")
                vo_words = []
        (self.dir / "vo_words.json").write_text(json.dumps(vo_words))
        self.set("VOICE", 1, f"narration {vo_dur:.1f}s, {len(vo_words)} words")
        self._check()

        # Build per-line video segments timed to VO word spans
        self.set("RENDER", 0.05, "cutting clips")
        # Map each line to a share of VO duration by character weight
        weights = [max(len(ln["text"]), 1) for ln in script["lines"]]
        total_w = sum(weights) or 1
        # leave 2.2s for title card at start
        title_dur = 2.2
        body_dur = max(vo_dur, target_sec * 0.5)
        # We'll stretch/compress video body to match VO; final mux uses VO length

        segs = []
        title_card = self.dir / "title_card.mp4"
        _card(title_card, script.get("title", title)[:42], tw, th, title_dur, "offcut recap")
        segs.append(title_card)

        # Split VO time across lines proportionally
        t_cursor = 0.0
        line_durs = [body_dur * (w / total_w) for w in weights]
        line_durs = [min(8.0, max(1.2, d)) for d in line_durs]
        scale = body_dur / (sum(line_durs) or 1)
        line_durs = [min(8.0, max(1.0, d * scale)) for d in line_durs]

        work = self.dir / "segs"
        work.mkdir(exist_ok=True)
        for i, (ln, ld) in enumerate(zip(script["lines"], line_durs)):
            self._check()
            raw = work / f"raw_{i:02d}.mp4"
            fit = work / f"fit_{i:02d}.mp4"
            _cut_clip(src, ln["clip_start"], ln["clip_end"], raw, tw, th, mute=True)
            _fit_clip_to_dur(raw, min(ld, 8.0), fit, tw, th)
            raw.unlink(missing_ok=True)
            # optional chapter card before line
            if ln.get("chapter") and (i == 0 or script["lines"][i - 1].get("chapter") != ln.get("chapter")):
                ch = work / f"ch_{i:02d}.mp4"
                _card(ch, str(ln["chapter"])[:32], tw, th, 1.4, "")
                segs.append(ch)
            segs.append(fit)
            self.set("RENDER", 0.05 + 0.5 * (i + 1) / len(script["lines"]), f"seg {i+1}/{len(script['lines'])}")

        # Concat video
        concat_list = self.dir / "concat.txt"
        concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))
        body = self.dir / "body.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "22", "-pix_fmt", "yuv420p", "-an", str(body)],
            check=True, capture_output=True)

        body_dur_actual = _probe_dur(body)
        # Pad or trim body to match title_dur + vo_dur for clean mux
        final_vid_dur = title_dur + vo_dur
        max_final = (90 if self.length == 1 else target_sec * 1.25) + title_dur
        if final_vid_dur > max_final:
            final_vid_dur = max_final
        sized = self.dir / "sized.mp4"
        if body_dur_actual < final_vid_dur:
            pad = min(final_vid_dur - body_dur_actual, 30.0)
            final_vid_dur = body_dur_actual + pad
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(body),
                 "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                 "-pix_fmt", "yuv420p", "-t", f"{final_vid_dur:.3f}", str(sized)],
                check=True, capture_output=True)
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(body), "-t", f"{final_vid_dur:.3f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                 "-pix_fmt", "yuv420p", str(sized)],
                check=True, capture_output=True)

        # Delay VO to start after title card; optional ducked bed from source (mostly silent OK)
        self.set("RENDER", 0.7, "mixing audio + captions")
        # Build VO with leading silence for title
        vo_delayed = self.dir / "vo_delayed.wav"
        delay_ms = int(title_dur * 1000)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(vo_wav),
             "-af", f"adelay={delay_ms}|{delay_ms}",
             "-t", f"{title_dur + vo_dur + 0.5:.3f}",
             str(vo_delayed)],
            check=True, capture_output=True)

        # Captions: offset words by title_dur
        cap_words = [{"w": w["w"], "s": w["s"] + title_dur, "e": w["e"] + title_dur} for w in vo_words]
        name = "clip_01"
        (self.dir / f"{name}.srt").write_text(captions.build_srt(cap_words))
        ass_path = self.dir / f"{name}.ass"
        if self.captions_on and cap_words:
            ass_path.write_text(captions.build_ass(cap_words, play_w=tw, play_h=th))

        # Final mux
        out_mp4 = self.dir / f"{name}.mp4"
        vf = f"ass={name}.ass" if self.captions_on and ass_path.exists() else "null"
        # duck: keep only narration (safer + silent-film friendly). Optional bed skipped for lean path.
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(sized), "-i", str(vo_delayed),
        ]
        if self.captions_on and ass_path.exists():
            cmd += ["-vf", f"ass={name}.ass"]
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest", "-movflags", "+faststart", str(out_mp4),
        ]
        subprocess.run(cmd, cwd=self.dir, check=True, capture_output=True)

        # thumb
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "3", "-i", str(out_mp4),
             "-frames:v", "1", "-vf", f"scale={270 if self.fmt == '9:16' else 480}:-2",
             f"{name}.jpg"],
            cwd=self.dir, check=True)

        dur = _probe_dur(out_mp4)
        self.clips.append({
            "name": name,
            "start": 0, "end": dur, "duration": round(dur, 1),
            "score": 90,
            "sub": {"hook": 8, "shock": 5, "comedy": 6, "summary": 9, "silence": 8},
            "why": f"Commentary recap ({script.get('_brain', '?')}); short clips + original narration.",
            "title": script.get("title", title),
            "description": script.get("description", ""),
            "hashtags": script.get("hashtags", []),
            "brain": script.get("_brain", "?"),
            "mp4": f"/files/{self.id}/{name}.mp4",
            "srt": f"/files/{self.id}/{name}.srt",
            "thumb": f"/files/{self.id}/{name}.jpg",
            "fmt": self.fmt,
        })
        self.set("RENDER", 1, f"{name}.mp4 ready ({dur:.1f}s)")

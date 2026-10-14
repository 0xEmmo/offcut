# offcut

Turn a long video into short vertical clips, on your own machine.

Paste a YouTube link or a local file. offcut transcribes it, picks the strongest
30–60s moments, reframes them to 9:16 around the speaker's face, and burns in
bold word-by-word captions (white, with emphasis words in yellow). Every clip
gets an **Audit** score and post copy you can paste straight into TikTok / Reels / Shorts.

**New in v0.2:**
- **WATCH** — follow YouTube channels; new uploads are clipped automatically
- **UPLOAD** — push a clip to YouTube Shorts (private by default) with one click

![input](docs/input.png)
![processing](docs/processing.png)
![results](docs/results.png)

## Run it

Needs Python 3.10+ and `ffmpeg` on your PATH.

```bash
./run.sh
```

First run creates `.venv` and installs everything, then opens http://localhost:7878.
The first transcription downloads the Whisper `base` model (~140 MB).

Headless, from the terminal:

```bash
./run.sh "https://www.youtube.com/watch?v=Y6bbMQXQ180" --count 4
./run.sh ~/Videos/podcast.mp4 --model gemini --no-captions
```

Output lands in `out/<job-id>/`: `clip_01.mp4`, `clip_01.srt`, a thumbnail and `job.json` with scores and copy.

## Channel watch

In the UI, under **WATCH**, paste an `@handle` or channel URL and hit **ADD**.
offcut resolves the channel id with yt-dlp, then polls the public RSS feed
(`https://www.youtube.com/feeds/videos.xml?channel_id=…`) every N minutes
(default 30, editable in the panel). Existing videos are marked seen on add so
you don't get a backlog dump — only *new* uploads start a job.

Toggle **auto-upload** per channel to push finished clips to YouTube Shorts
automatically (still private — see below). **POLL NOW** forces a check.

State lives in `data/watch.json` (gitignored).

## Connect YouTube + upload Shorts

Uploads use the YouTube Data API v3 (`videos.insert`) with a Desktop OAuth client.
Default privacy is **private** so you can review before going public. Each upload
appends `#Shorts` to the title and uses the generated description + tags.

### 1. Get `client_secret.json`

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a project (or pick one).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen**
   - User type: **External**
   - App name: `offcut` (anything)
   - Add your email as a developer contact
   - Publishing status: leave it in **Testing**
   - **Test users** → add the Google account you'll upload with
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON and save it as `client_secret.json` in the offcut folder
     (same directory as `run.sh`). The file is gitignored.

### 2. Connect

```bash
./run.sh
```

Click **CONNECT YOUTUBE** in the header. A browser window opens for Google
sign-in. Approve access; the token is saved to `data/youtube_token.json` (also
gitignored).

### 3. Upload

On any result clip, click **UPLOAD**. Or enable **auto-upload** on a watched
channel so finished clips go up on their own.

### Quota

The default YouTube API quota is **10,000 units/day**. Each `videos.insert`
costs **1,600 units**, so you get about **6 uploads per day** on the free tier.
Request a quota increase in Cloud Console if you need more.

### Stay safe on the platform

YouTube demonetises channels that just repost other people's clips with captions.
Add original commentary, a voiceover, or your own edit before publishing — or
only clip videos you own / have permission to use.

## The brain

Picking moments is the only step that benefits from an LLM. offcut uses the first one it finds:

| env var             | brain   | default model (override)                          |
|---------------------|---------|---------------------------------------------------|
| `ANTHROPIC_API_KEY` | claude  | claude-sonnet-4-5 (`OFFCUT_CLAUDE_MODEL`)         |
| `GEMINI_API_KEY`    | gemini  | gemini-2.5-flash (`OFFCUT_GEMINI_MODEL`)          |
| `GROQ_API_KEY`      | groq    | llama-3.3-70b-versatile (`OFFCUT_GROQ_MODEL`)     |
| ollama on :11434    | ollama  | llama3.1 (`OFFCUT_OLLAMA_MODEL`)                  |
| nothing             | no-key  | built-in heuristic                                |

You can also force one with the chips in the UI. If an LLM call fails, offcut
falls back to the heuristic instead of dying. The heuristic scores 45s windows on
speech density, dead air, emphasis words and how the window opens. It's decent, but it isn't taste.

Whisper size: `OFFCUT_WHISPER=small ./run.sh` (default `base`, CPU int8).

## How it works

```
INGEST      yt-dlp → source.mp4, ffprobe for size/duration
TRANSCRIPT  faster-whisper, word timestamps, VAD
SCENE       brain picks windows + sub-scores + copy
DIRECT      OpenCV Haar face detection every 0.5s, smoothed with a dead-zone → crop path
RENDER      ffmpeg crop → 1080x1920, ASS captions with a scale pop on the active word
WATCH       channel RSS every N min → new video ids → Job
UPLOAD      OAuth → videos.insert (private Short)
```

Progress is streamed to the browser over SSE (`/api/jobs/<id>/events`).

```
offcut/
  ingest.py          yt-dlp + ffprobe
  transcribe.py      faster-whisper
  brain.py           LLM providers + heuristic
  reframe.py         face track → ffmpeg crop expression
  captions.py        ASS + SRT
  pipeline.py        the job
  watch.py           channel poller + data/watch.json
  youtube_upload.py  OAuth + Shorts upload
  server.py          FastAPI + SSE
web/                 plain HTML/CSS/JS, no build step
data/                watch.json + youtube_token.json (gitignored)
```

## Known rough edges

- The face crop uses Haar cascades. That's fast but dated: it misses profiles, and with two people on screen it follows whoever's face is biggest. When faces are found in fewer than 30% of samples, it uses a centre crop.
- The crop moves in steps, held for 0.5s at a time, instead of easing between positions.
- Sub-scores from the heuristic are rough. Comedy especially is mostly guesswork without an LLM.
- BROWSE fills in the file name only, because browsers hide real paths. Paste the absolute path for files outside the working directory.
- YouTube sometimes asks yt-dlp to prove it isn't a bot. offcut retries three times, but if YouTube keeps asking you'll need cookies (`yt-dlp --cookies-from-browser`). Having Node installed helps yt-dlp.
- OAuth needs a real browser on the machine running offcut (installed-app flow). Headless servers need a different auth path.

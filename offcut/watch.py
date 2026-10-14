"""Channel watch: poll YouTube channels for new uploads and auto-clip them."""
from __future__ import annotations

import json, re, threading, time, xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "watch.json"
NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None


def _default():
    return {"poll_minutes": 30, "channels": [], "seen": [], "auto_jobs": []}


def load() -> dict:
    if not DATA.exists():
        return _default()
    try:
        d = json.loads(DATA.read_text())
    except Exception:
        return _default()
    for k, v in _default().items():
        d.setdefault(k, v)
    return d


def save(d: dict):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(DATA)


def resolve_channel(url_or_handle: str) -> dict:
    """Resolve @handle / channel URL to {id, handle, title, url} via yt-dlp."""
    import shutil, subprocess, sys
    raw = url_or_handle.strip()
    if raw.startswith("@"):
        raw = f"https://www.youtube.com/{raw}/videos"
    elif re.fullmatch(r"UC[\w-]{20,}", raw):
        raw = f"https://www.youtube.com/channel/{raw}/videos"
    elif not raw.startswith("http"):
        raw = f"https://www.youtube.com/@{raw.lstrip('@')}/videos"
    cmd = [sys.executable, "-m", "yt_dlp",
           *(["--js-runtimes", "node"] if shutil.which("node") else []),
           "-J", "--flat-playlist", "--playlist-end", "1", raw]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        msg = next((l for l in r.stderr.splitlines() if l.startswith("ERROR")), r.stderr.strip()[-200:])
        raise RuntimeError(f"resolve failed: {msg}")
    info = json.loads(r.stdout)
    cid = info.get("channel_id") or ""
    if not cid or cid == "NA":
        raise RuntimeError("no channel_id in yt-dlp output")
    title = info.get("channel") or info.get("uploader") or info.get("title") or cid
    uploader = info.get("uploader_id") or ""
    handle = uploader if str(uploader).startswith("@") else (f"@{uploader}" if uploader else "")
    # Prefer @handle from original input when present
    m = re.search(r"@([\w.-]+)", url_or_handle)
    if m:
        handle = "@" + m.group(1)
    return {
        "id": cid,
        "handle": handle,
        "title": title,
        "url": f"https://www.youtube.com/channel/{cid}",
    }


def fetch_rss(channel_id: str) -> list[dict]:
    """Latest uploads from the public channel RSS feed (no API key)."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    req = Request(url, headers={"User-Agent": "offcut/0.1"})
    with urlopen(req, timeout=20) as resp:
        xml = resp.read()
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS)
        title = entry.findtext("atom:title", default="", namespaces=NS)
        published = entry.findtext("atom:published", default="", namespaces=NS)
        link_el = entry.find("atom:link", NS)
        href = link_el.get("href") if link_el is not None else f"https://www.youtube.com/watch?v={vid}"
        if vid:
            out.append({"id": vid, "title": title, "published": published, "url": href})
    return out


def add_channel(url_or_handle: str, auto_upload: bool = False, seed_seen: bool = True) -> dict:
    info = resolve_channel(url_or_handle)
    with _lock:
        d = load()
        if any(c["id"] == info["id"] for c in d["channels"]):
            raise ValueError(f"already watching {info['title']}")
        ch = {**info, "auto_upload": bool(auto_upload), "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        d["channels"].append(ch)
        if seed_seen:
            try:
                for v in fetch_rss(info["id"]):
                    if v["id"] not in d["seen"]:
                        d["seen"].append(v["id"])
            except Exception:
                pass
        # keep seen list bounded
        d["seen"] = d["seen"][-500:]
        save(d)
    return ch


def remove_channel(channel_id: str):
    with _lock:
        d = load()
        d["channels"] = [c for c in d["channels"] if c["id"] != channel_id]
        save(d)


def set_auto_upload(channel_id: str, on: bool):
    with _lock:
        d = load()
        for c in d["channels"]:
            if c["id"] == channel_id:
                c["auto_upload"] = bool(on)
                save(d)
                return
        raise ValueError("channel not found")


def set_poll_minutes(n: int):
    with _lock:
        d = load()
        d["poll_minutes"] = max(5, min(int(n), 24 * 60))
        save(d)


def status() -> dict:
    d = load()
    return {
        "poll_minutes": d["poll_minutes"],
        "channels": d["channels"],
        "auto_jobs": list(reversed(d["auto_jobs"][-20:])),
        "seen_count": len(d["seen"]),
        "running": _thread is not None and _thread.is_alive(),
    }


def _start_job(video_url: str, video_id: str, title: str, channel: dict, register):
    """Kick off a clipping job; register(job) wires it into the server JOBS dict."""
    from .pipeline import Job
    job = Job(video_url, model="auto", captions=True, n=4)
    register(job)
    with _lock:
        d = load()
        d["auto_jobs"].append({
            "video_id": video_id, "title": title, "job_id": job.id,
            "channel_id": channel["id"], "channel": channel.get("title", ""),
            "auto_upload": bool(channel.get("auto_upload")),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "running",
        })
        d["auto_jobs"] = d["auto_jobs"][-50:]
        save(d)

    def run_then_maybe_upload():
        job.run()
        with _lock:
            d = load()
            for aj in d["auto_jobs"]:
                if aj["job_id"] == job.id:
                    aj["status"] = job.status
            save(d)
        if job.status == "done" and channel.get("auto_upload"):
            try:
                from . import youtube_upload as yt
                if yt.connected():
                    for clip in job.clips:
                        yt.upload_clip(job.dir / f"{clip['name']}.mp4", clip,
                                       privacy="private")
            except Exception as e:
                job.log.append(f"auto-upload: {e}")

    threading.Thread(target=run_then_maybe_upload, daemon=True).start()
    return job


def poll_once(register) -> list[dict]:
    """Check all channels; start jobs for unseen videos. Returns list of started jobs."""
    started = []
    with _lock:
        d = load()
        channels = list(d["channels"])
        seen = set(d["seen"])
    for ch in channels:
        try:
            videos = fetch_rss(ch["id"])
        except Exception as e:
            print(f"[watch] RSS fail {ch.get('title')}: {e}", flush=True)
            continue
        # oldest first so we process in publish order
        for v in reversed(videos):
            if v["id"] in seen:
                continue
            with _lock:
                d = load()
                if v["id"] in d["seen"]:
                    continue
                d["seen"].append(v["id"])
                d["seen"] = d["seen"][-500:]
                save(d)
                seen.add(v["id"])
            print(f"[watch] new: {v['title']} ({v['id']})", flush=True)
            job = _start_job(v["url"], v["id"], v["title"], ch, register)
            started.append({"job_id": job.id, "video_id": v["id"], "title": v["title"], "channel": ch["title"]})
    return started


def _loop(register):
    # first poll shortly after start
    time.sleep(5)
    while not _stop.is_set():
        try:
            poll_once(register)
        except Exception as e:
            print(f"[watch] poll error: {e}", flush=True)
        mins = load().get("poll_minutes", 30)
        _stop.wait(max(5, mins) * 60)


def start_poller(register):
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(register,), daemon=True, name="offcut-watch")
    _thread.start()


def stop_poller():
    _stop.set()

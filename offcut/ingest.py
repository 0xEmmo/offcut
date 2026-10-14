import shutil, subprocess, sys
from pathlib import Path


def ingest(source: str, workdir: Path) -> Path:
    """Return a local mp4 for a YouTube URL or a file path."""
    workdir.mkdir(parents=True, exist_ok=True)
    if source.startswith(("http://", "https://")):
        out = workdir / "source.%(ext)s"
        cmd = [sys.executable, "-m", "yt_dlp", *(["--js-runtimes", "node"] if shutil.which("node") else []), "-f",
               "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
               "--merge-output-format", "mp4", "--no-playlist", "-o", str(out), source]
        for attempt in range(3):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0: break
        else:
            msg = [l for l in r.stderr.splitlines() if l.startswith("ERROR")] or r.stderr.splitlines()[-1:]
            raise RuntimeError("yt-dlp: " + " ".join(msg)[:300])
        return workdir / "source.mp4"
    p = Path(source).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"no such file: {p}")
    dst = workdir / ("source" + p.suffix)
    shutil.copy(p, dst)
    return dst


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "default=nw=1", str(path)],
        check=True, capture_output=True, text=True).stdout
    kv = dict(l.split("=", 1) for l in out.split() if "=" in l)
    return {"w": int(kv["width"]), "h": int(kv["height"]), "duration": float(kv["duration"])}

import argparse, json, os, webbrowser
import uvicorn


def main():
    ap = argparse.ArgumentParser(prog="offcut")
    ap.add_argument("source", nargs="?", help="YouTube URL or video path (skip to start the web app)")
    ap.add_argument("--model", default="auto", choices=["auto", "claude", "gemini", "groq", "ollama", "heuristic"])
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", 7878)))
    a = ap.parse_args()
    if a.source:
        from .pipeline import Job
        j = Job(a.source, a.model, not a.no_captions, a.count); j.run()
        print(json.dumps(j.state(), indent=2)); return
    print(f"offcut → http://localhost:{a.port}")
    if not os.getenv("OFFCUT_NO_BROWSER"): webbrowser.open(f"http://localhost:{a.port}")
    uvicorn.run("offcut.server:app", host="127.0.0.1", port=a.port, log_level="warning")


main()

"""Pick clip windows. LLM if a key is set, heuristic otherwise."""
import json, os, re
import httpx

PROMPT = """You are a short-form video editor. Below is a transcript with [start] times in seconds.
Pick up to {n} self-contained moments, each 30-60 seconds, that would work as vertical clips.
Reply with JSON only: [{{"start":s,"end":s,"title":"","description":"","hashtags":["#x"],
"hook":0-10,"shock":0-10,"comedy":0-10,"summary":0-10,"silence":0-10,"why":"one sentence"}}]
(silence = how little dead air; higher is better)

{text}"""

EMPH = re.compile(r"^(never|always|crazy|insane|secret|best|worst|money|free|why|how|nobody|everyone|biggest|million|\d+[\w%$]*)$", re.I)


def available() -> str:
    if os.getenv("ANTHROPIC_API_KEY"): return "claude"
    if os.getenv("GEMINI_API_KEY"): return "gemini"
    if os.getenv("GROQ_API_KEY"): return "groq"
    try:
        httpx.get("http://localhost:11434/api/tags", timeout=0.5); return "ollama"
    except Exception:
        return "heuristic"


def _ask(brain: str, prompt: str) -> str:
    if brain == "claude":
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            json={"model": os.getenv("OFFCUT_CLAUDE_MODEL", "claude-sonnet-4-5"), "max_tokens": 4000,
                  "messages": [{"role": "user", "content": prompt}]})
        return r.raise_for_status().json()["content"][0]["text"]
    if brain == "gemini":
        m = os.getenv("OFFCUT_GEMINI_MODEL", "gemini-2.5-flash")
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                       params={"key": os.environ["GEMINI_API_KEY"]}, timeout=120,
                       json={"contents": [{"parts": [{"text": prompt}]}]})
        return r.raise_for_status().json()["candidates"][0]["content"]["parts"][0]["text"]
    if brain == "groq":
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=120,
                       headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                       json={"model": os.getenv("OFFCUT_GROQ_MODEL", "llama-3.3-70b-versatile"),
                             "messages": [{"role": "user", "content": prompt}]})
        return r.raise_for_status().json()["choices"][0]["message"]["content"]
    if brain == "ollama":
        r = httpx.post("http://localhost:11434/api/generate", timeout=600,
                       json={"model": os.getenv("OFFCUT_OLLAMA_MODEL", "llama3.1"), "prompt": prompt, "stream": False})
        return r.raise_for_status().json()["response"]
    raise ValueError(brain)


def _transcript_text(words):
    lines, buf, t0 = [], [], None
    for w in words:
        t0 = w["s"] if t0 is None else t0
        buf.append(w["w"])
        if w["w"].endswith((".", "?", "!")) or len(buf) > 25:
            lines.append(f"[{t0:.1f}] {' '.join(buf)}"); buf, t0 = [], None
    if buf: lines.append(f"[{t0:.1f}] {' '.join(buf)}")
    return "\n".join(lines)


def heuristic(words, duration, n=4):
    """Score sliding 45s windows by speech density, questions, emphasis and a strong opener."""
    if not words: return []
    cands = []
    t = 0.0
    while t + 30 <= max(duration, 30):
        end = min(t + 45, duration)
        ws = [w for w in words if t <= w["s"] < end]
        if ws:
            text = " ".join(w["w"] for w in ws)
            gaps = sum(max(0, b["s"] - a["e"]) for a, b in zip(ws, ws[1:]))
            density = len(ws) / (end - t)
            first = " ".join(w["w"] for w in ws[:12]).lower()
            hook = 4 + 3 * ("?" in first) + 2 * any(k in first for k in ("you", "why", "how", "never", "stop", "this"))
            shock = 3 + min(6, sum(bool(EMPH.match(w["w"].strip(".,!?"))) for w in ws) // 2)
            comedy = 2 + min(6, text.lower().count("haha") * 2 + text.lower().count("funny") * 2 + text.count("!"))
            summary = 4 + min(5, int(density * 1.5))
            silence = max(0, 10 - int(gaps / 2))
            cands.append({"start": ws[0]["s"], "end": ws[-1]["e"], "hook": min(hook, 10), "shock": shock,
                          "comedy": min(comedy, 10), "summary": min(summary, 10), "silence": silence,
                          "text": text})
        t += 10
    cands.sort(key=lambda c: -(c["hook"] + c["shock"] + c["comedy"] + c["summary"] + c["silence"]))
    picked = []
    for c in cands:
        if c["end"] - c["start"] < 25: continue
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
        if len(picked) == n: break
    for c in picked:
        ws = c.pop("text").split()
        title = " ".join(ws[:8]).rstrip(",.") + "…"
        kw = sorted({w.strip(".,!?").lower() for w in ws if len(w) > 6}, key=len, reverse=True)[:3]
        c.update(title=title, description=" ".join(ws[:30]) + "…",
                 hashtags=["#shorts", "#fyp"] + ["#" + re.sub(r"\W", "", k) for k in kw if re.sub(r"\W", "", k)],
                 why="Picked for steady speech and a strong opening line (no-key heuristic).")
    return sorted(picked, key=lambda c: c["start"])


def pick(words, duration, brain="auto", n=4):
    brain = available() if brain == "auto" else brain
    if brain != "heuristic":
        try:
            raw = _ask(brain, PROMPT.format(n=n, text=_transcript_text(words)))
            clips = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
            clips = [c for c in clips if 15 <= float(c["end"]) - float(c["start"]) <= 75]
            if clips: return brain, clips[:n]
        except Exception as e:
            print(f"[brain] {brain} failed ({e}); falling back to heuristic")
    return "heuristic", heuristic(words, duration, n)

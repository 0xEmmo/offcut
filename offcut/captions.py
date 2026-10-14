"""ASS captions: 3-word chunks, active word pops, emphasis words in yellow."""
import re
from .brain import EMPH

WHITE, YELLOW = "&H00FFFFFF&", "&H0000E5FF&"


def _head(play_w=1080, play_h=1920):
    # larger margin on vertical; lower third on landscape
    margin_v = 560 if play_h >= play_w else 120
    size = 128 if play_h >= play_w else 72
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def is_emph(w, i):
    clean = re.sub(r"[^\w%$]", "", w)
    return bool(EMPH.match(clean)) or (len(clean) >= 7) or i % 5 == 2


def chunks(words, size=3):
    for i in range(0, len(words), size):
        yield words[i:i + size]


def build_ass(words, play_w=1080, play_h=1920):
    out = [_head(play_w, play_h)]
    idx = 0
    for ch in chunks(words):
        for j, active in enumerate(ch):
            end = ch[j + 1]["s"] if j + 1 < len(ch) else active["e"]
            parts = []
            for k, w in enumerate(ch):
                col = YELLOW if is_emph(w["w"], idx + k) else WHITE
                txt = w["w"].upper()
                if k == j:
                    parts.append(f"{{\\c{col}\\fscx80\\fscy80\\t(0,90,\\fscx112\\fscy112)\\t(90,160,\\fscx104\\fscy104)}}{txt}{{\\r}}")
                else:
                    parts.append(f"{{\\c{col}\\alpha&H40&}}{txt}{{\\r}}")
            out.append(f"Dialogue: 0,{ts(active['s'])},{ts(max(end, active['s'] + 0.08))},Cap,,0,0,0,,{' '.join(parts)}")
        idx += len(ch)
    return "\n".join(out) + "\n"


def build_srt(words):
    def st(t):
        return f"{int(t//3600):02d}:{int(t%3600//60):02d}:{int(t%60):02d},{int(t*1000)%1000:03d}"
    lines = []
    for n, ch in enumerate(chunks(words, 6), 1):
        lines += [str(n), f"{st(ch[0]['s'])} --> {st(ch[-1]['e'])}", " ".join(w["w"] for w in ch), ""]
    return "\n".join(lines)

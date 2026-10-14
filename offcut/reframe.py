"""Find a smoothed horizontal crop centre that follows faces."""
import cv2, numpy as np


def face_track(path, start, end, src_w, src_h, step=0.5):
    """Return list of (t, x_centre) relative to clip start, or None if no faces."""
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(path))
    pts, t, hits = [], start, 0
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok: break
        small = cv2.resize(frame, (480, int(480 * src_h / src_w)))
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = casc.detectMultiScale(g, 1.1, 5, minSize=(30, 30))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            pts.append((x + w / 2) * src_w / 480); hits += 1
        else:
            pts.append(pts[-1] if pts else src_w / 2)
        t += step
    cap.release()
    if not pts or hits < len(pts) * 0.3: return None
    # exponential smoothing then a dead-zone so the frame doesn't jitter
    sm, cur = [], pts[0]
    for p in pts:
        cur = 0.8 * cur + 0.2 * p
        if not sm or abs(cur - sm[-1]) > src_w * 0.04: sm.append(cur)
        else: sm.append(sm[-1])
    return [(i * step, x) for i, x in enumerate(sm)]


def crop_expr(track, src_w, crop_w):
    """ffmpeg crop x expression from the track (piecewise, held between samples)."""
    if not track: return f"{(src_w - crop_w) // 2}"
    def clamp(x): return int(np.clip(x - crop_w / 2, 0, src_w - crop_w))
    expr = str(clamp(track[-1][1]))
    for t, x in reversed(track[:-1]):
        expr = f"if(lt(t\\,{t + 0.5:.2f})\\,{clamp(x)}\\,{expr})"
    return expr

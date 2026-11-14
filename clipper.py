import os, json, subprocess, textwrap, requests
from faster_whisper import WhisperModel

E = os.environ.get
URL = E("VIDEO_URL", "").strip()
N = int(E("NUM_CLIPS") or 5)
ORIENT = E("ORIENT") or "vertical"
LENGTH = E("LENGTH") or "short"
CAPS = E("CAPS") or "words"
BG = E("BG") or "blur"
LANG = E("LANG_CODE") or "auto"
TITLE_ON = (E("TITLE_ON") or "yes") == "yes"
BRAND = (E("BRAND") or "").strip()
RANGE = (E("RANGE") or "").strip()
KEY = E("GEMINI_API_KEY") or ""
LO, HI = {"short": (25, 60), "medium": (60, 100), "long": (120, 180)}[LENGTH]
FONT = "Noto Sans"
os.makedirs("out", exist_ok=True)


def run(cmd):
    subprocess.run(cmd, check=True)


def write_cookies():
    raw = E("YT_COOKIES") or ""
    fixed = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#"):
            fixed.append(ln)
        else:
            fixed.append("\t".join(ln.split()))
    if not fixed:
        return False
    with open("cookies.txt", "w") as f:
        f.write("\n".join(fixed) + "\n")
    return True


# 1. Download
if "drive.google.com" in URL:
    run(["gdown", "--fuzzy", URL, "-O", "video.mp4"])
else:
    cmd = ["yt-dlp", "--js-runtimes", "node",
           "-f", "bv*[height<=720]+ba/b[height<=720]/b",
           "--merge-output-format", "mp4", "-o", "video.mp4"]
    if write_cookies():
        cmd += ["--cookies", "cookies.txt"]
    run(cmd + [URL])

# 1b. Optional range (minutes), e.g. "10-40"
if "-" in RANGE:
    try:
        a, b = RANGE.split("-", 1)
        a, b = float(a or 0) * 60, float(b) * 60
        run(["ffmpeg", "-y", "-ss", str(a), "-i", "video.mp4", "-t", str(b - a),
             "-c", "copy", "trimmed.mp4"])
        os.replace("trimmed.mp4", "video.mp4")
    except Exception as ex:
        print("Range ignored:", ex)

dur = float(subprocess.check_output(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "csv=p=0", "video.mp4"]).decode().strip())

# 2. Transcribe (word level)
size = "small" if LANG in ("ur", "hi") else "base"
model = WhisperModel(size, device="cpu", compute_type="int8")
it, info = model.transcribe("video.mp4", language=None if LANG == "auto" else LANG,
                            vad_filter=True, word_timestamps=True)
segs, words = [], []
for s in it:
    segs.append((s.start, s.end, s.text.strip()))
    for w in (s.words or []):
        if w.word.strip():
            words.append((w.start, w.end, w.word.strip()))
print("Segments:", len(segs), "Words:", len(words))
transcript = "\n".join(f"[{int(a)}s] {t}" for a, b, t in segs)


# 3. Pick best moments with Gemini
def ask(prompt):
    for m in ["gemini-2.5-flash", "gemini-2.0-flash"]:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                params={"key": KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json"}},
                timeout=180)
            r.raise_for_status()
            data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [])
            return data
        except Exception as ex:
            print("Gemini failed:", m, ex)
    return None


prompt = (
    f"Below is a gaming video transcript with timestamps in seconds. Pick the {N} best moments "
    f"for short-form clips (funny, hype, shocking, big reactions). Each clip must be {LO} to {HI} "
    "seconds, must NOT overlap, and must start with a strong hook where a sentence begins. "
    "Titles and hashtags must use English letters only (English or Roman Urdu). "
    'Return ONLY JSON: [{"start": number, "end": number, "title": "catchy title max 8 words", '
    '"description": "1-2 sentences", "hashtags": "#a #b #c #d #e", "score": 1-10}]\n\n'
    + transcript[:150000])
picks = ask(prompt) if (KEY and segs) else None


def tags(x):
    return " ".join(x) if isinstance(x, list) else str(x)


clips = []
for p in (picks or []):
    try:
        s, e = float(p["start"]), float(p["end"])
        if s < 0 or s >= dur or not (LO * 0.6 <= e - s <= HI * 1.3):
            continue
        e = min(e, dur)
        if any(s < c[1] and e > c[0] for c in clips):
            continue
        clips.append((s, e, str(p.get("title", "Clip"))[:80], str(p.get("description", "")),
                      tags(p.get("hashtags", "")), float(p.get("score", 0))))
    except Exception:
        pass
clips.sort(key=lambda c: -c[5])
clips = clips[:N]

if not clips:
    print("Using fallback: evenly spaced clips")
    L = min((LO + HI) / 2, dur)
    step = dur / (N + 1)
    for i in range(N):
        s = max(0, min(step * (i + 1), dur - L))
        clips.append((s, s + L, f"Clip {i+1}", "", "#gaming #gta6 #shorts", 0))


# 4. Captions helpers
def ts(x):
    x = max(x, 0)
    return f"{int(x//3600):02d}:{int(x%3600//60):02d}:{x%60:06.3f}".replace(".", ",")


def write_srt(path, s, e):
    gsize = 2 if CAPS == "words" else 6
    ws = [w for w in words if w[1] > s and w[0] < e]
    groups, cur = [], []
    for w in ws:
        if cur and (len(cur) >= gsize or w[0] - cur[-1][1] > 0.7):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    with open(path, "w", encoding="utf-8") as f:
        for n, g in enumerate(groups, 1):
            a = max(g[0][0], s) - s
            b = max(min(g[-1][1], e) - s, a + 0.3)
            f.write(f"{n}\n{ts(a)} --> {ts(b)}\n{' '.join(x[2] for x in g)}\n\n")
    return len(groups)


# 5. Render
def render(i, clip, orient):
    s, e, title = clip[0], clip[1], clip[2]
    vert = orient == "vertical"
    W, H = (1080, 1920) if vert else (1920, 1080)
    os.makedirs(f"out/{orient}", exist_ok=True)
    if not vert:
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black[v0]")
    elif BG == "crop":
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v0]"
    elif BG == "black":
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black[v0]")
    else:
        base = (f"[0:v]split[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5[bg];"
                f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    chain = []
    if TITLE_ON and vert:
        open(f"t{i}.txt", "w", encoding="utf-8").write(textwrap.fill(title.upper(), 20))
        chain.append(f"drawtext=textfile=t{i}.txt:font='{FONT}':fontsize=72:fontcolor=white:"
                     "borderw=6:bordercolor=black:line_spacing=10:x=(w-text_w)/2:y=240:"
                     "enable='lt(t,4)'")
    if BRAND:
        open("brand.txt", "w", encoding="utf-8").write(BRAND)
        fs = 40 if vert else 34
        chain.append(f"drawtext=textfile=brand.txt:font='{FONT}':fontsize={fs}:"
                     "fontcolor=white@0.7:borderw=3:bordercolor=black@0.7:"
                     "x=w-text_w-40:y=h-text_h-80")
    if CAPS != "none" and has_caps[i]:
        if CAPS == "words":
            fs, col = (88, ",PrimaryColour=&H0000FFFF") if vert else (70, ",PrimaryColour=&H0000FFFF")
        else:
            fs, col = (64, "") if vert else (52, "")
        mv = 420 if vert else 70
        chain.append(f"subtitles=c{i}.srt:original_size={W}x{H}:"
                     f"force_style='FontName={FONT},FontSize={fs},Bold=1,Outline=5,Shadow=0,"
                     f"Alignment=2,MarginV={mv}{col}'")
    fc = base + ";[v0]" + ",".join(chain or ["null"]) + "[v]"
    out = f"out/{orient}/clip_{i}.mp4"
    run(["ffmpeg", "-y", "-ss", str(s), "-i", "video.mp4", "-t", str(e - s),
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out])


orients = ["vertical", "horizontal"] if ORIENT == "both" else [ORIENT]
has_caps = {}
lines = []
made = 0
for i, clip in enumerate(clips, 1):
    has_caps[i] = write_srt(f"c{i}.srt", clip[0], clip[1]) > 0 if CAPS != "none" else False
    for o in orients:
        try:
            render(i, clip, o)
            made += 1
        except Exception as ex:
            print("Render failed:", i, o, ex)
    subprocess.run(["ffmpeg", "-y", "-ss", str(clip[0] + 2), "-i", "video.mp4",
                    "-frames:v", "1", "-q:v", "2", f"out/thumb_{i}.jpg"])
    lines.append(f"clip_{i}\nTitle: {clip[2]}\nDescription: {clip[3]}\n"
                 f"Hashtags: {clip[4]}\nScore: {clip[5]}\n")

open("out/titles.txt", "w", encoding="utf-8").write("\n".join(lines))
print("Clips made:", made)
if made == 0:
    raise SystemExit(1)

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
STYLE = (E("EDIT_STYLE") or "auto").lower()
AUDIO_MODE = (E("AUDIO_MODE") or "original").lower()
VOICE_LANG = (E("VOICE_LANG") or "en").lower()
VOICE_GENDER = (E("VOICE_GENDER") or "auto").lower()
LO, HI = {"short": (25, 60), "medium": (60, 100), "long": (120, 180)}[LENGTH]
FONT = "Noto Sans"
os.makedirs("out", exist_ok=True)
report = []
if STYLE not in ("auto", "viral", "cinematic", "funny", "esports"):
    STYLE = "auto"


def note(message):
    print(message, flush=True)
    report.append(str(message))



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
    cmd = ["yt-dlp", "--js-runtimes", "node", "--remote-components", "ejs:github",
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
        note(f"Range ignored: {ex}")

dur = float(subprocess.check_output(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "csv=p=0", "video.mp4"]).decode().strip())

# 2. Transcribe (word level)
size = "small" if LANG in ("ur", "hi", "auto") else "base"
note(f"Whisper model: {size}")
note(f"Editing preset: {STYLE}; audio mode: {AUDIO_MODE}")
if AUDIO_MODE != "original":
    note("AI voiceover not yet implemented; original audio retained")
model = WhisperModel(size, device="cpu", compute_type="int8")
def transcribe(vad):
    it, info = model.transcribe(
        "video.mp4", language=None if LANG == "auto" else LANG,
        vad_filter=vad, word_timestamps=True)
    segs, words = [], []
    for segment in it:
        segs.append((segment.start, segment.end, segment.text.strip()))
        for word in segment.words or []:
            if word.word.strip() and word.start is not None and word.end is not None:
                words.append((word.start, word.end, word.word.strip()))
    return segs, words, info


segs, words, info = transcribe(True)
if not words:
    note("No word timestamps with VAD; retrying without VAD")
    segs, words, info = transcribe(False)
note(f"Detected language: {info.language}; segments: {len(segs)}; words: {len(words)}")
transcript = "\n".join(f"[{int(a)}s] {t}" for a, b, t in segs)


# 3. Pick best moments with Gemini
def list_models():
    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": KEY, "pageSize": 200}, timeout=45)
        response.raise_for_status()
        models = []
        for item in response.json().get("models", []):
            name = item.get("name", "").split("/")[-1]
            methods = item.get("supportedGenerationMethods", [])
            if ("generateContent" in methods and "flash" in name
                    and not any(x in name for x in
                                ("lite", "image", "tts", "live", "audio",
                                 "vision", "embedding", "robotics"))):
                models.append(name)
        return sorted(set(models), reverse=True)
    except Exception as exc:
        note(f"Gemini model discovery failed: {exc}")
        return []


def ask(prompt):
    models = list(dict.fromkeys(list_models() + ["gemini-2.5-flash"]))
    for name in models:
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent",
                params={"key": KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json"}},
                timeout=180)
            if not response.ok:
                note(f"Gemini {name}: HTTP {response.status_code} {response.text[:250]}")
                continue
            parts = response.json()["candidates"][0]["content"]["parts"]
            data = json.loads("".join(p.get("text", "") for p in parts))
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [])
            if not isinstance(data, list) or not data:
                note(f"Gemini {name}: empty or invalid picks")
                continue
            note(f"Gemini model used: {name}; proposed clips: {len(data)}")
            return data
        except Exception as exc:
            note(f"Gemini {name} failed: {exc}")
    return None


prompt = (
    f"Below is a gaming video transcript with timestamps in seconds. Pick the {N} best moments "
    f"for short-form clips (funny, hype, shocking, big reactions). Each clip must be {LO} to {HI} "
    "seconds, must NOT overlap, and must start with a strong hook where a sentence begins. "
    "Titles and hashtags must use English letters only (English or Roman Urdu). "
    'Return ONLY JSON: [{"start": number, "end": number, "title": "catchy title max 8 words", '
    '"description": "1-2 sentences", "hashtags": "#a #b #c #d #e", "score": 1-10}]\n\n'
    + transcript[:150000])
if not KEY:
    note("GEMINI_API_KEY missing; check repository Actions secrets")
if not segs:
    note("No speech detected; Gemini transcript selection skipped")
picks = ask(prompt) if (KEY and segs) else None


def tags(x):
    return " ".join(x) if isinstance(x, list) else str(x)


clips = []
for p in (picks or []):
    try:
        s, e = float(p["start"]), float(p["end"])
        if s < 0 or e > dur or s >= dur or not (LO * 0.6 <= e - s <= HI * 1.3):
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
    note("No valid Gemini picks; using speech-density fallback")
    clip_length = min((LO + HI) / 2, dur)
    candidates = []
    if words:
        t = 0.0
        while t + clip_length <= dur + 0.01:
            count = sum(1 for w in words if t <= w[0] < t + clip_length)
            candidates.append((count, t))
            t += 5
        candidates.sort(reverse=True)
    else:
        candidates = [(0, t) for t in
                      [i * clip_length for i in range(max(1, int(dur // clip_length)))]]
    for _, t in candidates:
        e = min(t + clip_length, dur)
        if e - t < min(LO * 0.6, dur):
            continue
        if any(t < c[1] and e > c[0] for c in clips):
            continue
        nearby = [text for a, b, text in segs if b > t and a < e]
        title = " ".join(" ".join(nearby).split()[:7]).strip(" .,!?:;")
        clips.append((t, e, title or f"Clip {len(clips)+1}",
                      "Highlight from the video", "#shorts #highlights", 0))
        if len(clips) >= N:
            break
    if not clips and dur > 0:
        clips = [(0, dur, "Video Highlight", "Highlight from the video",
                  "#shorts #highlights", 0)]
    note(f"Fallback clips selected: {len(clips)}")


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
            b = min(e - s, max(min(g[-1][1], e) - s, a + 0.3))
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
    preset = STYLE if STYLE != "auto" else ("esports" if clip[5] >= 8 else "viral")
    grades = {"viral": "eq=contrast=1.08:saturation=1.12",
              "cinematic": "eq=contrast=1.05:saturation=1.06",
              "funny": "eq=saturation=1.15",
              "esports": "eq=contrast=1.12:saturation=1.18"}
    chain.append(grades[preset])
    if TITLE_ON and vert:
        with open(f"t{i}.txt", "w", encoding="utf-8") as file:
            file.write(textwrap.fill(title.upper(), 20))
        chain.append(f"drawtext=textfile=t{i}.txt:font='{FONT}':fontsize=72:fontcolor=white:"
                     "borderw=6:bordercolor=black:line_spacing=10:x=(w-text_w)/2:y=240:"
                     "enable='lt(t,4)'")
    if BRAND:
        with open("brand.txt", "w", encoding="utf-8") as file:
            file.write(BRAND)
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
            thumb = f"out/{o}/thumb_{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i",
                            f"out/{o}/clip_{i}.mp4", "-frames:v", "1",
                            "-q:v", "2", thumb], check=True)
        except Exception as ex:
            note(f"Render failed: clip {i} {o}: {ex}")

    lines.append(f"clip_{i}\nTitle: {clip[2]}\nDescription: {clip[3]}\n"
                 f"Hashtags: {clip[4]}\nScore: {clip[5]}\n")

open("out/titles.txt", "w", encoding="utf-8").write("\n".join(lines))
note(f"Clips made: {made}")
with open("out/report.txt", "w", encoding="utf-8") as file:
    file.write("\n".join(report) + "\n")
if made == 0:
    raise SystemExit(1)

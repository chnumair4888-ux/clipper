import os, json, subprocess, textwrap, requests, asyncio, difflib
from faster_whisper import WhisperModel

E = os.environ.get
URL = E("VIDEO_URL", "").strip()
try:
    N = int(E("NUM_CLIPS") or 5)
except (TypeError, ValueError):
    N = 5
N = max(1, min(N, 10))
ORIENT = (E("ORIENT") or "vertical").lower()
if ORIENT not in ("vertical", "horizontal", "both"):
    ORIENT = "vertical"
LENGTH = (E("LENGTH") or "short").lower()
if LENGTH not in ("short", "medium", "long"):
    LENGTH = "short"
CAPS = (E("CAPS") or "words").lower()
if CAPS not in ("words", "lines", "none"):
    CAPS = "words"
LAYOUT = (E("LAYOUT") or "auto").lower()
if LAYOUT not in ("auto", "split", "fit", "crop"):
    LAYOUT = "auto"
BG = (E("BG") or "blur").lower()
if BG not in ("blur", "crop", "black"):
    BG = "blur"
LANG_RAW = (E("LANG_CODE") or "English").strip().lower()
LANG = {"english": "en", "en": "en",
        "roman urdu": "roman_urdu", "roman_urdu": "roman_urdu"}.get(LANG_RAW, "en")
TITLE_ON = (E("TITLE_ON") or "yes").lower() == "yes"
BRAND = (E("BRAND") or "").strip()
RANGE = (E("RANGE") or "").strip()
KEY = E("GEMINI_API_KEY") or ""
STYLE = (E("EDIT_STYLE") or "auto").lower()
if STYLE not in ("auto", "viral", "cinematic", "funny", "esports"):
    STYLE = "auto"
AUDIO_MODE = (E("AUDIO_MODE") or "original").lower()
if AUDIO_MODE not in ("original", "auto", "commentary", "music", "original+music"):
    AUDIO_MODE = "original"
VOICE_LANG = (E("VOICE_LANG") or "en").lower()
if VOICE_LANG not in ("en", "roman_urdu", "hi"):
    VOICE_LANG = "en"
VOICE_GENDER = (E("VOICE_GENDER") or "auto").lower()
if VOICE_GENDER not in ("auto", "male", "female"):
    VOICE_GENDER = "auto"
LO, HI = {"short": (25, 60), "medium": (60, 100), "long": (120, 180)}[LENGTH]
FONT = "Noto Sans"
os.makedirs("out", exist_ok=True)
report = []
if not URL:
    raise SystemExit("VIDEO_URL is required")


def note(message):
    safe = str(message).replace(KEY, "[REDACTED]") if KEY else str(message)
    print(safe, flush=True)
    report.append(safe)



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
           "-f", "bv*[height<=1080]+ba/b",
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

source_probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", "video.mp4"]).decode()
source_info = json.loads(source_probe)
source_video = next((st for st in source_info.get("streams", []) if st.get("codec_type") == "video"), {})
dur = float(source_info.get("format", {}).get("duration", 0) or 0)
note(f"Source ffprobe: {source_video.get('width')}x{source_video.get('height')} codec={source_video.get('codec_name')} duration={dur:.2f}s")


# Transcribe the source video before highlight selection.
# The same timestamps are reused later for captions.
note(f"Editing preset: {STYLE}; audio mode: {AUDIO_MODE}")
transcript = ""
segs, words = [], []
GLOSSARY_PATH = "glossary.txt"
glossary_terms, glossary_map = [], {}
if os.path.exists(GLOSSARY_PATH):
    for raw_line in open(GLOSSARY_PATH, encoding="utf-8"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            wrong, right = [x.strip() for x in line.split("=>", 1)]
            if wrong and right:
                glossary_map[wrong.lower()] = right
                glossary_terms.append(right)
        else:
            glossary_terms.append(line)

def fix_glossary_word(text):
    if not glossary_map:
        return text
    key = text.strip().lower()
    if key in glossary_map:
        return glossary_map[key]
    close = difflib.get_close_matches(key, list(glossary_map), n=1, cutoff=0.86)
    return glossary_map[close[0]] if close else text

def transcribe_full_video():
    global transcript

    audio_path = "full_video_audio.wav"
    try:
        note("Preparing full-video audio for highlight analysis")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
             "-i", "video.mp4", "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", audio_path],
            check=True, timeout=900
        )

        size = "small"
        note(f"Full-video Whisper model: {size}; language: {LANG}")
        model = WhisperModel(size, device="cpu", compute_type="int8")

        romanizer = None
        if LANG == "roman_urdu":
            import uroman
            romanizer = uroman.Uroman()

        segments, info = model.transcribe(
            audio_path,
            language="ur" if LANG == "roman_urdu" else "en",
            task="transcribe",
            vad_filter=True,
            word_timestamps=True,
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt=("Gaming terms: " + ", ".join(glossary_terms[:80])) if glossary_terms else None,
            hotwords=" ".join(glossary_terms[:80]) if glossary_terms else None
        )

        transcript_lines = []
        for segment in segments:
            segment_text = ' '.join(fix_glossary_word(w) for w in segment.text.strip().split())
            if romanizer:
                segment_text = romanizer.romanize_string(
                    segment_text, lcode="urd"
                )

            if segment_text:
                transcript_lines.append(
                    f"[{segment.start:.1f}-{segment.end:.1f}] {segment_text}"
                )
                segs.append((
                    segment.start, segment.end, segment_text
                ))

            for word in segment.words or []:
                if word.word.strip() and word.start is not None and word.end is not None:
                    word_text = fix_glossary_word(word.word.strip())
                    if romanizer:
                        word_text = romanizer.romanize_string(
                            word_text, lcode="urd"
                        )
                    words.append((
                        word.start,
                        word.end,
                        word_text
                    ))

        transcript = "\n".join(transcript_lines)
        note(
            f"Full-video speech: {len(segs)} segments; "
            f"{len(words)} words; transcript characters: {len(transcript)}"
        )

    except Exception as exc:
        note(f"Full-video transcription unavailable: {type(exc).__name__}")
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


transcribe_full_video()

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
        note(f"Gemini model discovery failed: {type(exc).__name__}")
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
            note(f"Gemini {name} failed: {type(exc).__name__}")
    return None



# Visual highlight analysis: sample gameplay frames even when nobody speaks.
def visual_picks():
    if not KEY:
        return None
    import base64
    from PIL import Image, ImageOps, ImageDraw
    import io
    interval = max(5.0, dur / 24)
    stamps = [round(i * interval, 1) for i in range(min(24, max(1, int(dur / interval))))]
    thumbs = []
    for index, at in enumerate(stamps):
        try:
            frame = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-ss", str(at),
                 "-i", "video.mp4", "-frames:v", "1",
                 "-vf", "scale=320:180:force_original_aspect_ratio=decrease",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                capture_output=True, timeout=25, check=True).stdout
            img = Image.open(io.BytesIO(frame)).convert("RGB")
            tile = Image.new("RGB", (340, 214), "#141414")
            tile.paste(img, ((340-img.width)//2, 24))
            ImageDraw.Draw(tile).text((8, 5), f"{at:.1f}s", fill="white")
            thumbs.append(tile)
        except Exception as exc:
            note(f"Frame sample {index} unavailable: {type(exc).__name__}")
    if not thumbs:
        return None
    columns = 4
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 340, rows * 214), "#101010")
    for i, tile in enumerate(thumbs):
        sheet.paste(tile, ((i % columns) * 340, (i // columns) * 214))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=72)
    os.makedirs("out", exist_ok=True)
    sheet.save("out/visual_contact_sheet.jpg", "JPEG", quality=72)
    prompt = (
        f"Analyze these timestamped gaming frames sampled from a {dur:.1f}-second video. "
        f"Select up to {N} non-overlapping engaging moments of {LO}-{HI} seconds each. "
        "Use visual evidence: fights, movement, dramatic scene changes, discoveries, "
        "fails, wins. Frames are sparse, so do NOT invent kills, wins or events not "
        "visually supported. Pick timestamps inside video duration. "
        "Score confidence 1-10. For ambiguous footage use neutral accurate titles. "
        "Return only JSON array with keys start,end,title,description,hashtags,score. "
        "Transcript if available: " + transcript[:9000]
    )
    for model_name in list(dict.fromkeys(list_models() + ["gemini-2.5-flash"])):
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
                headers={"x-goog-api-key": KEY},
                json={"contents": [{"parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg",
                                     "data": base64.b64encode(buf.getvalue()).decode("ascii")}}
                ]}], "generationConfig": {"responseMimeType": "application/json"}},
                timeout=65)
            if not response.ok:
                note(f"Visual Gemini {model_name}: HTTP {response.status_code}")
                continue
            raw = "".join(p.get("text", "") for p in
                          response.json()["candidates"][0]["content"]["parts"])
            result = json.loads(raw)
            if isinstance(result, dict):
                result = next((v for v in result.values() if isinstance(v, list)), [])
            if isinstance(result, list) and result:
                note(f"Visual Gemini model: {model_name}; candidates: {len(result)}")
                return result
        except Exception as exc:
            note(f"Visual Gemini failed: {type(exc).__name__}")
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
picks = visual_picks() if KEY else None
if not picks:
    note("Visual AI unavailable; using transcript-based fallback where speech is available")


def tags(x):
    return " ".join(x) if isinstance(x, list) else str(x)


clips = []
valid_picks = []

for item in (picks or []):
    try:
        if not isinstance(item, dict):
            continue

        start_time = float(item["start"])
        end_time = float(item["end"])
        score = float(item.get("score", 0))

        if not all(map(lambda x: __import__("math").isfinite(x),
                       (start_time, end_time, score))):
            continue

        duration = end_time - start_time

        if start_time < 0 or end_time > dur or start_time >= dur:
            continue
        if not (LO * 0.6 <= duration <= HI * 1.3):
            continue
        if not (1 <= score <= 10):
            continue

        title = " ".join(str(item.get("title", "Gaming Highlight")).split())[:80]
        description = " ".join(str(item.get("description", "")).split())[:500]
        hashtags = tags(item.get("hashtags", ""))[:250]

        valid_picks.append((
            start_time, end_time, title or "Gaming Highlight",
            description, hashtags, score
        ))

    except (TypeError, ValueError, KeyError, OverflowError):
        continue

# Highest-confidence clips first; reject overlaps and near-duplicates.
valid_picks.sort(key=lambda clip: clip[5], reverse=True)

for candidate in valid_picks:
    s_time, e_time = candidate[0], candidate[1]

    if any(
        s_time < existing[1] + 2 and e_time > existing[0] - 2
        for existing in clips
    ):
        continue

    clips.append(candidate)

    if len(clips) >= N:
        break

note(f"Gemini picks received: {len(picks or [])}; valid non-overlapping clips: {len(clips)}")
if not clips:
    note("No valid Gemini picks; scoring speech candidates (no timeline-from-zero fallback)")
    clip_length = min((LO + HI) / 2, dur)
    candidates = []

    if words:
        t = 0.0
        while t + clip_length <= dur + 0.01:
            count = sum(
                1 for w in words
                if t <= w[0] < t + clip_length
            )
            # Do not select windows with no detected speech.
            if count > 0:
                candidates.append((count, t))
            t += 5

        if not candidates:
            raise SystemExit("Stage-1 candidate scoring failed: no speech candidate windows detected; refusing timeline fallback.")

    if not candidates:
        raise SystemExit("Stage-1 candidate scoring failed; refusing to cut from video start.")

    candidates.sort(reverse=True)

    for _, t in candidates:
        e = min(t + clip_length, dur)
        if e - t < min(LO * 0.6, dur):
            continue
        if any(t < c[1] and e > c[0] for c in clips):
            continue

        nearby = [
            text for a, b, text in segs
            if b > t and a < e
        ]
        title = " ".join(
            " ".join(nearby).split()[:7]
        ).strip(" .,!?:;")

        clips.append((
            t, e,
            title or f"Clip {len(clips)+1}",
            "Highlight from the video",
            "#shorts #highlights",
            0
        ))

        if len(clips) >= N:
            break

    if not clips:
        raise SystemExit("No usable highlights selected; refusing to export a full-video fallback.")

    note(f"Fallback clips selected: {len(clips)}")

# 4. Captions helpers
def ts(x):
    x = max(x, 0)
    return f"{int(x//3600):02d}:{int(x%3600//60):02d}:{x%60:06.3f}".replace(".", ",")


def write_srt(path, s, e):
    gsize = 3 if CAPS == "words" else 5
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


def ass_timestamp(seconds):
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    remainder = seconds % 60
    return f"{hours}:{minutes:02d}:{remainder:05.2f}"


def animated_captions(path, start, end, vertical):
    # Larger, high-contrast captions with a karaoke fill color.
    width, height = (1080, 1920) if vertical else (1920, 1080)
    font_size, bottom = (86, 360) if vertical else (48, 75)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Pop,{FONT},{font_size},&H00FFFFFF,&H0000D7FF,&H00000000,"
        f"&H80000000,-1,0,0,0,100,100,0,0,1,6,0,2,130,130,{bottom},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    visible = [w for w in words if w[1] > start and w[0] < end]
    groups, current = [], []
    for word in visible:
        if current and (len(current) >= 3 or word[0] - current[-1][1] > 0.65):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for group in groups:
            a = max(0.0, group[0][0] - start)
            b = min(end - start, max(group[-1][1] - start, a + 0.65))
            if b <= a:
                continue
            karaoke = []
            for index, word in enumerate(group):
                word_start = max(start, word[0])
                word_end = min(end, word[1])
                if index + 1 < len(group):
                    next_start = max(word_end, min(end, group[index + 1][0]))
                else:
                    next_start = word_end
                duration_cs = max(1, min(600, round((next_start - word_start) * 100)))
                clean = str(word[2]).upper().replace("\\", "").replace("{", "(").replace("}", ")")
                clean = clean.replace("\\N", " ")
                karaoke.append(f"{{\\kf{duration_cs}}}{clean}")
            caption = " ".join(karaoke)
            f.write(f"Dialogue: 0,{ass_timestamp(a)},{ass_timestamp(b)},Pop,,0,0,0,,{caption}\n")
    return len(groups)


# 5. Render
def detect_layout():
    if LAYOUT in ("split", "fit", "crop"):
        note(f"Layout selected: {LAYOUT} (explicit override)")
        return LAYOUT, None
    try:
        import cv2
        import numpy as np
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        boxes = []
        sampled = 0
        for t in [dur * x / 9 for x in range(1, 9)]:
            frame = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", "video.mp4", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, check=True, timeout=20).stdout
            image = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                continue
            sampled += 1
            faces = cascade.detectMultiScale(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(max(40, image.shape[1]//12), max(40, image.shape[0]//12)))
            if len(faces):
                boxes.append(max(faces, key=lambda b: b[2]*b[3]))
        if sampled and len(boxes)/sampled >= 0.6:
            # Median face rectangle across samples; pad around the head.
            box = tuple(int(np.median([b[k] for b in boxes])) for k in range(4))
            note(f"Layout selected: split (face detected in {len(boxes)}/{sampled} samples)")
            return "split", box
        note(f"Layout selected: fit (face detection {len(boxes)}/{sampled} samples below threshold)")
        return "fit", None
    except Exception as exc:
        note(f"Layout selected: fit (face detection unavailable: {type(exc).__name__})")
        return "fit", None

LAYOUT_RESULT, FACE_BOX = detect_layout()

def render(i, clip, orient):
    s, e, title = clip[0], clip[1], clip[2]
    vert = orient == "vertical"
    W, H = (1080, 1920) if vert else (1920, 1080)
    os.makedirs(f"out/{orient}", exist_ok=True)
    layout = LAYOUT_RESULT if vert else "fit"
    if not vert:
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black[v0]")
    elif layout == "split" and FACE_BOX:
        x, y, fw, fh = FACE_BOX
        source_w = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", "video.mp4"]).decode().strip())
        source_h = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", "video.mp4"]).decode().strip())
        pad = int(max(fw, fh) * 0.7)
        x, y = max(0, x-pad), max(0, y-pad)
        fw, fh = min(source_w-x, fw+2*pad), min(source_h-y, fh+2*pad)
        base = (f"[0:v]split[a][b];[a]crop={fw}:{fh}:{x}:{y},scale=1080:960[top];"
                f"[b]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[game];"
                "[top][game]vstack[v0]")
    elif layout == "crop" or BG == "crop":
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v0]"
    else:
        base = (f"[0:v]split[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5[bg];"
                f"[b]scale={W}:960:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    chain = []
    preset = STYLE if STYLE != "auto" else ("esports" if clip[5] >= 8 else "viral")
    grades = {"viral": "eq=contrast=1.08:saturation=1.12",
              "cinematic": "eq=contrast=1.05:saturation=1.06",
              "funny": "eq=saturation=1.15",
              "esports": "eq=contrast=1.12:saturation=1.18"}
    chain.append(grades[preset])
    # Restrained punch-in for high-confidence highlights only.
    # This is a gentle editorial effect, not AI event tracking or smart reframing.
    if vert and clip[5] >= 8 and preset in ("viral", "esports", "cinematic"):
        zoom = {"viral": 1.035, "esports": 1.05, "cinematic": 1.025}[preset]
        zw, zh = int(W * zoom) // 2 * 2, int(H * zoom) // 2 * 2
        chain.append(
            f"scale={zw}:{zh},crop={W}:{H}:"
            "x='(in_w-out_w)/2+(in_w-out_w)*0.035*sin(t*0.7)':"
            "y='(in_h-out_h)/2+(in_h-out_h)*0.025*sin(t*0.55)'"
        )
    if TITLE_ON and vert and not title.lower().startswith(("clip ", "video highlight")):
        title_words = " ".join(title.upper().split()[:8])
        with open(f"t{i}.txt", "w", encoding="utf-8") as file:
            file.write(title_words)
        chain.append("drawbox=x=0:y=180:w=1080:h=180:color=red@0.95:t=fill")
        chain.append(f"drawtext=textfile=t{i}.txt:font='{FONT}':fontsize=64:fontcolor=white:"
                      "borderw=3:bordercolor=black:x=(w-text_w)/2:y=235:"
                      "enable='lt(t,2.8)'")
    if BRAND:
        with open("brand.txt", "w", encoding="utf-8") as file:
            file.write(BRAND)
        fs = 40 if vert else 34
        chain.append(f"drawtext=textfile=brand.txt:font='{FONT}':fontsize={fs}:"
                     "fontcolor=white@0.7:borderw=3:bordercolor=black@0.7:"
                     "x=130:y=h-text_h-360")
    if CAPS != "none" and has_caps[i]:
        if CAPS == "words":
            fs, col = (44, "") if vert else (36, "")
        else:
            fs, col = (64, "") if vert else (52, "")
        mv = 170 if vert else 50
        if CAPS == "words":
            chain.append(f"subtitles=c{i}.ass")
        else:
            chain.append(f"subtitles=c{i}.srt:original_size={W}x{H}:"
                         f"force_style='FontName={FONT},FontSize={fs},Bold=1,Outline=5,"
                         f"Shadow=0,Alignment=2,MarginV={mv}{col}'")
    # Short, subtle video fades make clip boundaries feel intentional.
    clip_duration = max(0.0, e - s)
    fade_duration = min(0.18, clip_duration / 4)
    if fade_duration > 0:
        fade_out_start = max(0.0, clip_duration - fade_duration)
        chain.append(
            f"fade=t=in:st=0:d={fade_duration:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}"
        )
    fc = base + ";[v0]" + ",".join(chain or ["null"]) + "[v]"
    out = f"out/{orient}/clip_{i}.mp4"
    audio_present = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", "video.mp4"],
        capture_output=True, text=True, check=True).stdout.strip()
    audio_opts = (["-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
                  if audio_present else [])
    run(["ffmpeg", "-y", "-ss", str(s), "-i", "video.mp4", "-t", str(e - s),
         "-filter_complex", fc, "-map", "[v]", *audio_opts,
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])


# Optional AI-generated short narration, synthesized with Edge TTS.
def narration_text(clip):
    if not KEY:
        note("Commentary needs GEMINI_API_KEY; keeping original audio")
        return None
    language = {"en": "English", "roman_urdu": "Roman Urdu (Latin letters)",
                "hi": "Hindi in Devanagari"}.get(VOICE_LANG, "English")
    prompt = (
        f"Write exactly ONE short, exciting gaming commentator sentence in {language}. "
        "Maximum 16 words. Do not claim an event occurred unless it is evident "
        "from the transcript. No hashtags, emoji, markdown or quotes. "
        f"Clip title: {clip[2]}. "
        f"Transcript: {' '.join(t for a,b,t in segs if b > clip[0] and a < clip[1])[:1600]}"
    )
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{(list_models() or ['gemini-2.5-flash'])[0]}:generateContent",
            params={"key": KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60)
        response.raise_for_status()
        value = "".join(part.get("text", "") for part in
                        response.json()["candidates"][0]["content"]["parts"]).strip()
        return value[:240] or None
    except Exception as exc:
        note(f"Commentary text unavailable: {type(exc).__name__}")
        return None


def add_commentary(path, clip, index):
    # Narration is only available in the explicit commentary modes.
    # Music-only and music-mix modes must not unexpectedly synthesize a voice.
    if AUDIO_MODE not in ("auto", "commentary"):
        return False
    if AUDIO_MODE == "auto" and clip[5] < 7:
        return False
    narration = narration_text(clip)
    if not narration:
        return False
    voices = {
        "en": ("en-US-GuyNeural", "en-US-JennyNeural"),
        "roman_urdu": ("ur-PK-AsadNeural", "ur-PK-UzmaNeural"),
        "hi": ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural")
    }
    male, female = voices.get(VOICE_LANG, voices["en"])
    voice = male if VOICE_GENDER == "male" else female
    if VOICE_GENDER == "auto":
        voice = male
    try:
        import edge_tts
        voice_path = f"voice_{index}.mp3"
        asyncio.run(edge_tts.Communicate(narration, voice).save(voice_path))
        temp = path + ".vo.mp4"
        # Mix the generated narration over quieter original gameplay audio.
        has_audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True).stdout.strip()
        if has_audio:
            audio_args = ["-filter_complex",
                          "[0:a]volume=0.22[game];[1:a]volume=1.5[voice];"
                          "[game][voice]amix=inputs=2:duration=first:dropout_transition=0[a]",
                          "-map", "0:v", "-map", "[a]"]
        else:
            audio_args = ["-map", "0:v", "-map", "1:a"]
        run(["ffmpeg", "-y", "-i", path, "-i", voice_path, *audio_args,
             "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
             "-t", str(clip[1] - clip[0]), "-movflags", "+faststart", temp])
        os.replace(temp, path)
        note(f"AI commentary added to clip {index}: {narration}")
        return True
    except Exception as exc:
        note(f"AI voice unavailable; original audio preserved: {type(exc).__name__}")
        return False



# Optional royalty-cleared background music. Add only tracks with documented
# licenses to music/<style>/ and record source/terms in music/LICENSES.md.
def add_music(path, clip, index, preset):
    if AUDIO_MODE not in ("music", "original+music"):
        return False
    folder = os.path.join("music", preset)
    allowed = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
    tracks = sorted(
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.lower().endswith(allowed)
    ) if os.path.isdir(folder) else []
    if not tracks:
        note(f"No licensed music track found in {folder}; leaving clip audio unchanged")
        return False
    track = tracks[(index - 1) % len(tracks)]
    temp = path + ".music.mp4"
    duration = max(0.1, clip[1] - clip[0])
    try:
        has_audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True).stdout.strip()
        if AUDIO_MODE == "music" or not has_audio:
            audio_filter = (
                "[1:a]volume=0.72,afade=t=in:st=0:d=0.35,"
                "afade=t=out:st=" + f"{max(0.0, duration-0.6):.3f}:d=0.6[a]"
            )
        else:
            audio_filter = (
                "[1:a]volume=0.38[bgm];"
                "[bgm][0:a]sidechaincompress=threshold=0.025:ratio=8:attack=25:release=450[ducked];"
                "[0:a]volume=1.0[orig];"
                "[orig][ducked]amix=inputs=2:duration=first:dropout_transition=2,"
                "alimiter=limit=0.89[a]"
            )
        run(["ffmpeg", "-y", "-i", path, "-stream_loop", "-1", "-i", track,
             "-filter_complex", audio_filter, "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.3f}",
             "-movflags", "+faststart", temp])
        os.replace(temp, path)
        note(f"Background music added: {os.path.basename(track)} ({AUDIO_MODE})")
        return True
    except Exception as exc:
        if os.path.exists(temp):
            os.remove(temp)
        note(f"Music mix failed; keeping existing audio: {type(exc).__name__}")
        return False

def validate_output(path, orient, expected_duration):
    """Check the encoded deliverable with ffprobe before counting it as complete."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path],
        capture_output=True, text=True, check=True)
    data = json.loads(probe.stdout)
    video = next((x for x in data.get("streams", []) if x.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("Output has no video stream")
    expected_size = (1080, 1920) if orient == "vertical" else (1920, 1080)
    actual_size = (int(video.get("width", 0)), int(video.get("height", 0)))
    if actual_size != expected_size:
        raise RuntimeError(f"Unexpected output size {actual_size}; expected {expected_size}")
    if video.get("codec_name") != "h264":
        raise RuntimeError(f"Unexpected video codec: {video.get('codec_name')}")
    if video.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"Unexpected pixel format: {video.get('pix_fmt')}")
    actual_duration = float(data.get("format", {}).get("duration", 0) or 0)
    if actual_duration <= 0 or abs(actual_duration - expected_duration) > 1.5:
        raise RuntimeError(f"Output duration mismatch: {actual_duration:.2f}s")
    bitrate = int(data.get("format", {}).get("bit_rate", 0) or 0)
    if bitrate < 6000000:
        raise RuntimeError(f"Output bitrate below 6 Mbps: {bitrate}")
    audio = any(x.get("codec_type") == "audio" for x in data.get("streams", []))
    return {
        "file": path, "video_codec": video.get("codec_name"),
        "pixel_format": video.get("pix_fmt"), "width": actual_size[0],
        "height": actual_size[1], "duration_seconds": round(actual_duration, 3),
        "frame_rate": video.get("avg_frame_rate"), "bitrate": bitrate,
        "audio_present": audio, "status": "passed"
    }


orients = ["vertical", "horizontal"] if ORIENT == "both" else [ORIENT]
has_caps = {}
lines = []
quality_results = []
made = 0
for i, clip in enumerate(clips, 1):
    has_caps[i] = write_srt(f"c{i}.srt", clip[0], clip[1]) > 0 if CAPS != "none" else False
    for o in orients:
        try:
            if CAPS == "words" and has_caps[i]:
                animated_captions(f"c{i}.ass", clip[0], clip[1], o == "vertical")
            render(i, clip, o)
            output_path = f"out/{o}/clip_{i}.mp4"
            add_commentary(output_path, clip, i)
            selected_preset = STYLE if STYLE != "auto" else ("esports" if clip[5] >= 8 else "viral")
            add_music(output_path, clip, i, selected_preset)
            try:
                quality_result = validate_output(output_path, o, clip[1] - clip[0])
            except Exception:
                # Never leave a failed export in the upload directory.
                if os.path.exists(output_path):
                    os.remove(output_path)
                raise
            quality_results.append(quality_result)
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
with open("out/quality_report.json", "w", encoding="utf-8") as quality_file:
    json.dump(quality_results, quality_file, ensure_ascii=False, indent=2)
with open("out/metadata.json", "w", encoding="utf-8") as metadata:
    json.dump([
        {"clip": i, "start": c[0], "end": c[1], "title": c[2],
         "description": c[3], "hashtags": c[4], "score": c[5],
         "style": STYLE, "audio": AUDIO_MODE}
        for i, c in enumerate(clips, 1)
    ], metadata, ensure_ascii=False, indent=2)
note(f"Clips made: {made}")
with open("out/report.txt", "w", encoding="utf-8") as file:
    file.write("\n".join(report) + "\n")
if made == 0:
    raise SystemExit(1)

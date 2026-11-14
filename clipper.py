import os, json, subprocess, textwrap, requests, asyncio
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

# Visual-first highlight selection; speech is transcribed only after selection.
note(f"Editing preset: {STYLE}; audio mode: {AUDIO_MODE}")
transcript = ""
segs, words = [], []

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
    note("Visual AI unavailable; using timeline fallback without full-video transcription")


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


# 3b. Transcribe selected clips only; avoid full-video Whisper analysis.
if clips and (CAPS != "none" or AUDIO_MODE != "original"):
    try:
        size = "base" if LANG in ("auto", "ur", "hi") else "tiny"
        note(f"Whisper model: {size}; selected clips only")
        model = WhisperModel(size, device="cpu", compute_type="int8")
        for clip_index, (s, e, *_rest) in enumerate(clips, 1):
            audio_path = f"selected_audio_{clip_index}.wav"
            try:
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                                "-ss", str(s), "-i", "video.mp4", "-t", str(e-s),
                                "-vn", "-ac", "1", "-ar", "16000", audio_path],
                               check=True, timeout=120)
                segments, info = model.transcribe(
                    audio_path, language=None if LANG == "auto" else LANG,
                    vad_filter=False, word_timestamps=True, beam_size=3,
                    condition_on_previous_text=False)
                for segment in segments:
                    segs.append((s + segment.start, s + segment.end, segment.text.strip()))
                    for word in segment.words or []:
                        if word.word.strip() and word.start is not None and word.end is not None:
                            words.append((s + word.start, s + word.end, word.word.strip()))
            except Exception as exc:
                note(f"Clip {clip_index} speech unavailable: {type(exc).__name__}")
            finally:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
        note(f"Selected speech: {len(segs)} segments; {len(words)} words")
    except Exception as exc:
        note(f"Speech analysis unavailable: {type(exc).__name__}")

# 4. Captions helpers
def ts(x):
    x = max(x, 0)
    return f"{int(x//3600):02d}:{int(x%3600//60):02d}:{x%60:06.3f}".replace(".", ",")


def write_srt(path, s, e):
    gsize = 5 if CAPS == "words" else 7
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
    width, height = (720, 1280) if vertical else (1280, 720)
    font_size, bottom = (44, 170) if vertical else (36, 55)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Pop,{FONT},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H80000000,-1,0,0,0,100,100,0,0,1,5,1,2,50,50,{bottom},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    visible = [w for w in words if w[1] > start and w[0] < end]
    groups, current = [], []
    for word in visible:
        if current and (len(current) >= 5 or word[0] - current[-1][1] > 0.65):
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
            caption = " ".join(w[2] for w in group)
            caption = caption.replace("\\", "").replace("{", "(").replace("}", ")")
            caption = caption.replace("\\N", " ")
            f.write(f"Dialogue: 0,{ass_timestamp(a)},{ass_timestamp(b)},Pop,,0,0,0,,{caption}\n")
    return len(groups)


# 5. Render
def render(i, clip, orient):
    s, e, title = clip[0], clip[1], clip[2]
    vert = orient == "vertical"
    W, H = (720, 1280) if vert else (1280, 720)
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
    # Subtle motion/punch-in for action-heavy presets.
    if False:  # Disable distracting automatic gameplay zoom
        zoom = 1.10 if preset == "esports" else 1.06
        zw, zh = int(W * zoom) // 2 * 2, int(H * zoom) // 2 * 2
        chain.append(
            f"scale={zw}:{zh},crop={W}:{H}:"
            "x='(in_w-out_w)/2+(in_w-out_w)*0.15*sin(t*1.4)':"
            "y='(in_h-out_h)/2+(in_h-out_h)*0.10*sin(t*1.1)'"
        )
    if TITLE_ON and vert and not title.lower().startswith(("clip ", "video highlight")):
        with open(f"t{i}.txt", "w", encoding="utf-8") as file:
            file.write(textwrap.fill(title, 28))
        chain.append(f"drawtext=textfile=t{i}.txt:font='{FONT}':fontsize=32:fontcolor=white:"
                     "borderw=3:bordercolor=black:line_spacing=6:x=(w-text_w)/2:y=90:"
                     "enable='lt(t,2.8)'")
    if BRAND:
        with open("brand.txt", "w", encoding="utf-8") as file:
            file.write(BRAND)
        fs = 40 if vert else 34
        chain.append(f"drawtext=textfile=brand.txt:font='{FONT}':fontsize={fs}:"
                     "fontcolor=white@0.7:borderw=3:bordercolor=black@0.7:"
                     "x=w-text_w-40:y=h-text_h-80")
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
    fc = base + ";[v0]" + ",".join(chain or ["null"]) + "[v]"
    out = f"out/{orient}/clip_{i}.mp4"
    audio_present = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", "video.mp4"],
        capture_output=True, text=True, check=True).stdout.strip()
    audio_opts = (["-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
                  if audio_present else [])
    run(["ffmpeg", "-y", "-ss", str(s), "-i", "video.mp4", "-t", str(e - s),
         "-r", "30",
         "-filter_complex", fc, "-map", "[v]", *audio_opts,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out])


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
    if AUDIO_MODE == "original":
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


orients = ["vertical", "horizontal"] if ORIENT == "both" else [ORIENT]
has_caps = {}
lines = []
made = 0
for i, clip in enumerate(clips, 1):
    has_caps[i] = write_srt(f"c{i}.srt", clip[0], clip[1]) > 0 if CAPS != "none" else False
    for o in orients:
        try:
            if CAPS == "words" and has_caps[i]:
                animated_captions(f"c{i}.ass", clip[0], clip[1], o == "vertical")
            render(i, clip, o)
            add_commentary(f"out/{o}/clip_{i}.mp4", clip, i)
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
with open("out/metadata.json", "w", encoding="utf-8") as metadata:
    json.dump([
        {"clip": i, "start": c[0], "end": c[1], "title": c[2],
         "description": c[3], "hashtags": c[4], "score": c[5],
         "style": STYLE, "audio": "original"}
        for i, c in enumerate(clips, 1)
    ], metadata, ensure_ascii=False, indent=2)
note(f"Clips made: {made}")
with open("out/report.txt", "w", encoding="utf-8") as file:
    file.write("\n".join(report) + "\n")
if made == 0:
    raise SystemExit(1)

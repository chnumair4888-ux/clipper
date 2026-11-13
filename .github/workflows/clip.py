import os, json, subprocess, requests
from faster_whisper import WhisperModel

URL = os.environ["VIDEO_URL"].strip()
N = int(os.environ.get("NUM_CLIPS") or 5)
KEY = os.environ.get("GEMINI_API_KEY", "")
MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]
os.makedirs("out", exist_ok=True)

def run(cmd):
    subprocess.run(cmd, check=True)

# 1. Download
if "drive.google.com" in URL:
    run(["gdown", "--fuzzy", URL, "-O", "video.mp4"])
else:
    run(["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]/b",
         "--merge-output-format", "mp4", "-o", "video.mp4", URL])

dur = float(subprocess.check_output(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "csv=p=0", "video.mp4"]).decode().strip())

# 2. Transcribe
model = WhisperModel("base", device="cpu", compute_type="int8")
segs, _ = model.transcribe("video.mp4", vad_filter=True)
segs = [(s.start, s.end, s.text.strip()) for s in segs]
transcript = "\n".join(f"[{int(a)}s] {t}" for a, b, t in segs)

# 3. Pick best moments
def ask(prompt):
    for m in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                params={"key": KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json"}},
                timeout=180)
            r.raise_for_status()
            return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as e:
            print("Gemini failed:", m, e)
    return None

prompt = (f"Below is a video transcript with timestamps in seconds. Pick the {N} most "
          "exciting, funny or shareable moments for YouTube Shorts. Each must be 30 to 60 "
          "seconds, must not overlap, and must start where a sentence starts. "
          'Return ONLY JSON: [{"start": number, "end": number, "title": "short catchy title"}]\n\n'
          + transcript[:150000])
picks = ask(prompt) if (KEY and segs) else None

clips = []
for p in (picks or []):
    try:
        s, e = float(p["start"]), float(p["end"])
        if 10 <= e - s <= 90 and 0 <= s < dur:
            clips.append((s, min(e, dur), str(p.get("title", "clip"))))
    except Exception:
        pass
if not clips:  # fallback: evenly spaced 45s clips
    step = dur / (N + 1)
    clips = [(step * (i + 1), min(step * (i + 1) + 45, dur), f"Clip {i+1}") for i in range(N)]
clips = clips[:N]

# 4. Cut, vertical, captions
def ts(x):
    x = max(x, 0)
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

titles = []
for i, (s, e, title) in enumerate(clips, 1):
    with open(f"c{i}.srt", "w", encoding="utf-8") as f:
        n = 1
        for a, b, t in segs:
            if b > s and a < e and t:
                f.write(f"{n}\n{ts(max(a, s) - s)} --> {ts(min(b, e) - s)}\n{t}\n\n")
                n += 1
    vf = ("[0:v]split[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
          "[b]scale=1080:-2[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
          f"subtitles=c{i}.srt:force_style='FontName=Noto Sans,FontSize=13,Bold=1,Outline=2,Alignment=2,MarginV=260'[v]")
    run(["ffmpeg", "-y", "-ss", str(s), "-i", "video.mp4", "-t", str(e - s),
         "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", f"out/clip_{i}.mp4"])
    titles.append(f"clip_{i}.mp4: {title}")

open("out/titles.txt", "w", encoding="utf-8").write("\n".join(titles))
print("Done")

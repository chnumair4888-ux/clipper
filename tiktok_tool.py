#!/usr/bin/env python3
"""Clipper TikTok tool (runs on your Oracle VM).

  python3 tiktok_tool.py url                 -> prints the TikTok login link (saves a random 'state')
  python3 tiktok_tool.py exchange            -> paste code + state from the callback page, saves tokens
  python3 tiktok_tool.py whoami              -> shows the connected TikTok display name
  python3 tiktok_tool.py status              -> token expiry info (no secrets printed)
  python3 tiktok_tool.py upload FILE [FILE]  -> uploads MP4s to the TikTok inbox as DRAFTS (video.upload)
  python3 tiktok_tool.py upload-manifest     -> uploads the vertical clips listed in ~/clipper/public_clips/manifest.json

Secrets live ONLY in ~/.clipper_tiktok.env (chmod 600) and ~/.clipper_tiktok_tokens.json (chmod 600).
Never commit those files. Direct Post is NOT used.
"""
import argparse, fcntl, hmac, json, os, secrets, sys, time, urllib.parse
from pathlib import Path
import requests

HOME = Path.home()
ENV_FILE = Path(os.environ.get("TIKTOK_ENV_FILE", HOME / ".clipper_tiktok.env"))
TOKEN_FILE = Path(os.environ.get("TIKTOK_TOKEN_FILE", HOME / ".clipper_tiktok_tokens.json"))
STATE_FILE = HOME / ".clipper_tiktok_state.json"
LOG_FILE = HOME / ".clipper_tiktok_uploads.json"
API = os.environ.get("TIKTOK_API_BASE", "https://open.tiktokapis.com").rstrip("/")
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload"
CHUNK = 10_000_000                      # same size TikTok uses in its own example
DAILY_LIMIT = 5                         # TikTok: max 5 pending shares per 24h
INIT_GAP = float(os.environ.get("TIKTOK_INIT_GAP", "12"))      # init is limited to 6/min per token
POLL = float(os.environ.get("TIKTOK_POLL_SECONDS", "5"))


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"HTTP {status} {code}: {message}")


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def load_cfg():
    cfg = {}
    if ENV_FILE.exists():
        if ENV_FILE.stat().st_mode & 0o077:
            print(f"WARNING: run  chmod 600 {ENV_FILE}", file=sys.stderr)
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    keys = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI")
    for k in keys:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        die(f"missing {', '.join(missing)} in {ENV_FILE}")
    return cfg


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load_tokens():
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        die("no tokens yet: run  url  then  exchange")


def token_request(cfg, data):
    body = {"client_key": cfg["TIKTOK_CLIENT_KEY"], "client_secret": cfg["TIKTOK_CLIENT_SECRET"], **data}
    try:
        r = requests.post(f"{API}/v2/oauth/token/", data=body, timeout=60,
                          headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
        j = r.json()
    except Exception as e:
        die(f"token request failed ({type(e).__name__})")
    if "access_token" not in j:
        die(f"TikTok refused: {j.get('error')} - {j.get('error_description') or j.get('message')}")
    return j


def store_tokens(j):
    now = int(time.time())
    t = {"access_token": j["access_token"], "refresh_token": j["refresh_token"],
         "access_expires_at": now + int(j.get("expires_in", 7200)),
         "refresh_expires_at": now + int(j.get("refresh_expires_in", 15552000)),
         "open_id": j.get("open_id", ""), "scope": j.get("scope", "")}
    save_json(TOKEN_FILE, t)          # saved immediately: refresh tokens ROTATE, the old one dies
    return t


def get_access_token(cfg):
    lock = open(str(TOKEN_FILE) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # two processes must never refresh at the same time
    try:
        t = load_tokens()
        if t["access_expires_at"] - time.time() > 300:
            return t["access_token"]
        if t["refresh_expires_at"] <= time.time():
            die("refresh token expired: run  url  and  exchange  again")
        return store_tokens(token_request(cfg, {"grant_type": "refresh_token",
                                                "refresh_token": t["refresh_token"]}))["access_token"]
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def api(method, path, token, **kw):
    r = requests.request(method, API + path, timeout=60, **kw,
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    try:
        j = r.json()
    except ValueError:
        j = {}
    err = j.get("error") or {}
    if r.status_code >= 400 or err.get("code") not in (None, "ok"):
        raise ApiError(r.status_code, err.get("code"), err.get("message"))
    return j.get("data", {})


# ---------------- login ----------------
def cmd_url(_):
    cfg = load_cfg()
    state = secrets.token_urlsafe(24)
    save_json(STATE_FILE, {"state": state, "created": time.time()})
    q = urllib.parse.urlencode({"client_key": cfg["TIKTOK_CLIENT_KEY"], "scope": SCOPES, "response_type": "code",
                                "redirect_uri": cfg["TIKTOK_REDIRECT_URI"], "state": state})
    print("Open this link in the browser where you are logged in to the TikTok account:\n")
    print(f"{AUTH_URL}?{q}\n")
    print("After you approve, the callback page shows a code and a state. Then run:  exchange")


def cmd_exchange(_):
    cfg = load_cfg()
    try:
        saved = json.loads(STATE_FILE.read_text())
    except Exception:
        die("no saved state: run  url  first")
    code = urllib.parse.unquote(input("code  : ").strip())
    state = input("state : ").strip()
    if not hmac.compare_digest(state, saved["state"]):
        die("state does not match: this login attempt is NOT trusted. Run  url  again.")
    if time.time() - saved["created"] > 600:
        die("state is older than 10 minutes. Run  url  again.")
    STATE_FILE.unlink(missing_ok=True)
    t = store_tokens(token_request(cfg, {"grant_type": "authorization_code", "code": code,
                                         "redirect_uri": cfg["TIKTOK_REDIRECT_URI"]}))
    print("Connected. Scopes granted:", t["scope"])
    if "video.upload" not in t["scope"]:
        print("WARNING: video.upload was NOT granted, uploads will fail.")
    cmd_whoami(None)


def cmd_whoami(_):
    cfg = load_cfg()
    d = api("GET", "/v2/user/info/", get_access_token(cfg), params={"fields": "open_id,avatar_url,display_name"})
    u = d.get("user", d)
    print("TikTok account:", u.get("display_name"))


def cmd_status(_):
    t = load_tokens()
    now = time.time()
    print("access token valid for  :", max(0, int(t["access_expires_at"] - now) // 60), "min")
    print("refresh token valid for :", max(0, int(t["refresh_expires_at"] - now) // 86400), "days")
    print("scopes                  :", t["scope"])
    print("uploads in last 24h     :", len(recent_uploads()), "of", DAILY_LIMIT)


# ---------------- upload ----------------
def recent_uploads():
    try:
        log = json.loads(LOG_FILE.read_text())
    except Exception:
        log = []
    return [x for x in log if x > time.time() - 86400]


def record_upload():
    log = recent_uploads()
    log.append(time.time())
    save_json(LOG_FILE, log)


def upload_one(token, path):
    size = path.stat().st_size
    if size <= 0:
        raise ApiError(0, "empty_file", str(path.name))
    total = max(1, size // CHUNK)                       # TikTok rule: floor(size / chunk_size), min 1
    chunk = CHUNK if total > 1 else size                # single piece = whole file
    d = api("POST", "/v2/post/publish/inbox/video/init/", token, json={"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total}})
    publish_id, url = d["publish_id"], d["upload_url"]  # url must be used exactly as returned
    record_upload()                                     # it now counts as a pending share
    with open(path, "rb") as f:
        for i in range(total):
            start = i * chunk
            end = size - 1 if i == total - 1 else start + chunk - 1   # last chunk takes the remainder
            f.seek(start)
            data = f.read(end - start + 1)
            for attempt in range(4):
                try:
                    r = requests.put(url, data=data, timeout=300, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                    code = r.status_code
                except requests.RequestException:
                    code = 599
                if code in (200, 201, 206):
                    break
                if code >= 500 and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ApiError(code, "chunk_upload_failed", f"chunk {i + 1}/{total}")
    return publish_id


def wait_status(token, publish_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = api("POST", "/v2/post/publish/status/fetch/", token, json={"publish_id": publish_id})
        st = d.get("status")
        if st in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"):
            return st, d.get("fail_reason")
        time.sleep(POLL)
    return "TIMEOUT", None


def cmd_upload(a):
    files = [Path(p).expanduser() for p in a.files]
    for p in files:
        if not p.is_file():
            die(f"file not found: {p}")
    do_upload(files, a.force)


def cmd_upload_manifest(a):
    mp = Path(a.manifest or os.environ.get("CLIPPER_MANIFEST") or HOME / "clipper" / "public_clips" / "manifest.json").expanduser()
    try:
        m = json.loads(mp.read_text())
    except Exception:
        die(f"cannot read manifest: {mp}")
    items = m if isinstance(m, list) else (m.get("clips") or [])
    names = [(i.get("filename") if isinstance(i, dict) else str(i)) for i in items]
    files = [mp.parent / n for n in names if n]
    files = [f for f in (([f for f in files if "vertical" in f.name.lower()]) or files) if f.is_file()]   # TikTok = vertical
    if not files:
        die("manifest lists no clip files that exist on this server")
    print(f"{len(files)} clip(s) from manifest")
    do_upload(files, a.force)


def do_upload(files, force):
    cfg = load_cfg()
    used = len(recent_uploads())
    if used + len(files) > DAILY_LIMIT and not force:
        die(f"TikTok allows only {DAILY_LIMIT} pending drafts per 24h; {used} already used. "
            f"Post or delete drafts in TikTok, or use --force at your own risk.")
    results, ok = [], True
    for n, p in enumerate(files):
        if n:
            time.sleep(INIT_GAP)
        try:
            token = get_access_token(cfg)
            pid = upload_one(token, p)
            st, why = wait_status(token, pid)
        except ApiError as e:
            st, why, pid = "ERROR", str(e), None
        except Exception as e:
            st, why, pid = "ERROR", type(e).__name__, None
        good = st in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE")
        ok &= good
        print(f"{'OK  ' if good else 'FAIL'} {p.name}: {st}" + (f" ({why})" if why else ""))
        results.append({"file": p.name, "publish_id": pid, "status": st, "reason": why})
    if ok:
        print("Draft(s) sent. Open the TikTok app, tap the inbox notification, add caption and post.")
    print("RESULT " + json.dumps(results))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("url", cmd_url), ("exchange", cmd_exchange), ("whoami", cmd_whoami), ("status", cmd_status)):
        sub.add_parser(name).set_defaults(fn=fn)
    up = sub.add_parser("upload")
    up.add_argument("files", nargs="+")
    up.add_argument("--force", action="store_true")
    up.set_defaults(fn=cmd_upload)
    um = sub.add_parser("upload-manifest")
    um.add_argument("--manifest")
    um.add_argument("--force", action="store_true")
    um.set_defaults(fn=cmd_upload_manifest)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

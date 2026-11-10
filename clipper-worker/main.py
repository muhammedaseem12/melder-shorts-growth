import os, uuid, time, threading, subprocess, tempfile, shutil, requests, hmac, hashlib, time as clock
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from urllib.parse import urlparse

app = FastAPI(title="Melder Clip Worker")
app.add_middleware(CORSMiddleware, allow_origins=["https://melder-shorts-growth.vercel.app"], allow_credentials=False, allow_methods=["GET","POST","OPTIONS"], allow_headers=["Authorization","Content-Type"])
SECRET = os.getenv("CLIPPER_WORKER_SECRET", "")
BASE = Path(tempfile.gettempdir()) / "melder-jobs"
BASE.mkdir(exist_ok=True)
jobs = {}
lock = threading.Lock()
processing_lock = threading.Semaphore(1)

class JobRequest(BaseModel):
    videoUrl: HttpUrl
    clipCount: int = 5
    clipLength: str = "30-45"
    aspectRatio: str = "9:16"
    rightsConfirmed: bool = False

def auth(value):
    if not SECRET or value != f"Bearer {SECRET}":
        raise HTTPException(401, "Unauthorized worker request")

def detect_highlights(source, duration, clip_length, count, folder):
    """Memory-bounded highlight ranking using streamed audio and downscaled scene analysis."""
    import array, math, re
    proc = subprocess.Popen(["ffmpeg", "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1", "-i", str(source), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    per_second, pending = [], b""
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk: break
            pending += chunk
            while len(pending) >= 16000:
                block, pending = pending[:16000], pending[16000:]
                samples = array.array("h"); samples.frombytes(block)
                if samples:
                    rms = math.sqrt(sum(x*x for x in samples) / len(samples))
                    per_second.append(20 * math.log10(max(rms, 1) / 32768))
        if len(pending) >= 2:
            pending = pending[:len(pending) - (len(pending) % 2)]
            samples = array.array("h"); samples.frombytes(pending)
            if samples:
                rms = math.sqrt(sum(x*x for x in samples) / len(samples))
                per_second.append(20 * math.log10(max(rms, 1) / 32768))
        if proc.wait(timeout=900) != 0: raise ValueError("Could not analyse this video's audio track.")
    finally:
        if proc.stdout: proc.stdout.close()
        if proc.poll() is None: proc.kill()
    if not per_second: raise ValueError("No usable audio track was found in this video.")
    scene = subprocess.run(["ffmpeg", "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1", "-i", str(source), "-vf", "fps=1,scale=480:-2,select=gt(scene\\,0.22),showinfo", "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=900)
    scene_times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", scene.stderr)]
    baseline = sorted(per_second)[max(0, int(len(per_second) * 0.15) - 1)]
    candidates = []; step = max(5, clip_length // 3); last = max(0, int(duration - clip_length))
    for start in range(0, last + 1, step):
        end = min(duration, start + clip_length); a = max(0, int(start)); b = min(len(per_second), max(a + 1, int(end)))
        vals = per_second[a:b]
        if not vals: continue
        active = sum(1 for v in vals if v > baseline + 5) / len(vals)
        mean = sum(vals) / len(vals)
        variation = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        scenes = sum(1 for t in scene_times if start <= t < end)
        scene_rate = min(1.0, scenes / max(1, clip_length / 8))
        score = max(0, min(100, round(45 * active + 30 * min(1, variation / 12) + 25 * scene_rate)))
        candidates.append({"start": start, "score": score})
    candidates.sort(key=lambda x: x["score"], reverse=True); chosen = []; min_gap = max(clip_length * 0.65, 20)
    for candidate in candidates:
        if all(abs(candidate["start"] - item["start"]) >= min_gap for item in chosen): chosen.append(candidate)
        if len(chosen) >= count: break
    if not chosen: raise ValueError("Could not identify highlight sections. Try a video with clear audio.")
    return sorted(chosen, key=lambda x: x["start"])
def run_job(job_id, payload, uploaded_source=None):
    # One video at a time keeps peak RAM within the small worker memory limit.
    with lock:
        if job_id in jobs:
            jobs[job_id].update(status="queued", progress=0, message="Waiting for the clip worker to finish the current video…")
    processing_lock.acquire()
    with lock:
        if job_id in jobs:
            jobs[job_id].update(status="processing", progress=1, processingStartedAt=time.time(), message="Worker started. Preparing video analysis…")
    try:
        _run_job_locked(job_id, payload, uploaded_source)
    finally:
        processing_lock.release()

def _run_job_locked(job_id, payload, uploaded_source=None):
    folder = BASE / job_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        url = str(payload["videoUrl"])
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        youtube_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
        if parsed.scheme != "https" or not host:
            raise ValueError("Please use a valid HTTPS video link.")
        if uploaded_source is not None:
            source = Path(uploaded_source)
            if not source.is_file():
                raise ValueError("The uploaded source video is missing. Please submit the video link again.")
        elif host in youtube_hosts:
            if not payload.get("rightsConfirmed"):
                raise ValueError("Confirm that you own this video or have permission to create clips from it.")
            import yt_dlp
            source_template = str(folder / "source.%(ext)s")
            with lock: jobs[job_id].update(progress=3, message="Connecting to the permitted YouTube source…")
            def download_progress(info):
                if info.get("status") == "downloading":
                    total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                    done = info.get("downloaded_bytes") or 0
                    pct = int(3 + 12 * done / total) if total else 5
                    with lock:
                        if job_id in jobs:
                            jobs[job_id].update(progress=min(15, pct), message="Downloading source video…")
            options = {
                "format": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
                "outtmpl": source_template,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "max_filesize": 250 * 1024 * 1024,
                "socket_timeout": 20,
                "retries": 2,
                "fragment_retries": 2,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [download_progress],
            }
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl.extract_info(url, download=True)
                candidates = [p for p in folder.glob("source.*") if p.is_file() and p.suffix not in (".part", ".ytdl")]
                if not candidates:
                    raise ValueError("The source video could not be downloaded. Check that downloading is allowed for this video.")
                source = max(candidates, key=lambda p: p.stat().st_size)
                if source.stat().st_size > 250 * 1024 * 1024:
                    raise ValueError("Video exceeds the 500 MB processing limit.")
            except Exception as exc:
                raise ValueError("Could not retrieve this YouTube video. It may be private, restricted, unavailable, or not downloadable. " + str(exc)[:220])
        else:
            source = folder / "source.bin"
            with requests.get(url, stream=True, timeout=(20, 180), headers={"User-Agent":"MelderClipper/1.0"}) as response:
                response.raise_for_status()
                ctype = response.headers.get("content-type", "")
                if "text/html" in ctype.lower(): raise ValueError("That URL serves a webpage. Paste a direct MP4/WebM URL.")
                total = 0
                with source.open("wb") as out:
                    for chunk in response.iter_content(1024*1024):
                        if chunk:
                            total += len(chunk)
                            if total > 250*1024*1024: raise ValueError("Video exceeds the 250 MB processing limit.")
                            out.write(chunk)
        probe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(source)],capture_output=True,text=True,check=True)
        duration = float(probe.stdout.strip())
        if duration < 8: raise ValueError("Video is too short to clip.")
        low, high = [int(x) for x in payload["clipLength"].split("-")]
        length = min(high, max(low, 30))
        count = min(20, max(1, int(payload["clipCount"])))
        count = min(count, max(1, int(duration // max(8, length*0.65))))
        ratio = payload.get("aspectRatio", "9:16")
        vf = {"9:16":"scale=540:960:force_original_aspect_ratio=increase,crop=540:960", "1:1":"scale=720:720:force_original_aspect_ratio=increase,crop=720:720", "16:9":"scale=960:540:force_original_aspect_ratio=increase,crop=960:540"}.get(ratio, "scale=540:960:force_original_aspect_ratio=increase,crop=540:960")
        with lock: jobs[job_id].update(progress=8,message="Analysing audio and scene changes to find standout moments…")
        candidates=detect_highlights(source,duration,length,count,folder)
        clips=[]
        for i,candidate in enumerate(candidates):
            start=candidate["start"]
            out=folder/f"clip-{i+1}.mp4"
            subprocess.run(["ffmpeg","-threads","1","-filter_threads","1","-filter_complex_threads","1","-y","-ss",str(start),"-i",str(source),"-t",str(min(length,duration-start)),"-vf",vf,"-c:v","libx264","-threads","1","-preset","ultrafast","-crf","26","-c:a","aac","-b:a","96k","-movflags","+faststart",str(out)],capture_output=True,check=True,timeout=900)
            clips.append({"title":f"Highlight {i+1}","hook":"Picked from audio activity and visual scene changes","duration":round(min(length,duration-start)),"score":candidate["score"],"downloadUrl":f"/jobs/{job_id}/clips/{i+1}"})
            with lock: jobs[job_id].update(progress=10+int((i+1)/len(candidates)*90),clips=clips,message=f"Rendering highlight {i+1} of {len(candidates)}…")
        with lock: jobs[job_id].update(status="completed",progress=100,message="Highlights are ready. This free offline detector ranks audio activity and scene changes; it does not understand spoken words or guarantee viral hooks.",clips=clips)
    except Exception as e:
        with lock: jobs[job_id].update(status="failed",message=str(e),progress=0)


def verify_upload_token(value):
    try:
        exp, sig = value.split(".", 1)
        if int(exp) < int(clock.time()) or int(exp) > int(clock.time()) + 660:
            raise ValueError()
        expected = hmac.new(SECRET.encode(), f"upload:{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): raise ValueError()
    except Exception:
        raise HTTPException(401, "Upload link expired or invalid. Please try again.")

@app.post("/jobs/upload")
async def upload_job(
    file: UploadFile = File(...),
    clipCount: int = Form(5),
    clipLength: str = Form("30-45"),
    aspectRatio: str = Form("9:16"),
    authorization: str = Header(default="")
):
    verify_upload_token(authorization.replace("Bearer ", "", 1))
    if clipCount not in (5, 10, 15, 20): raise HTTPException(400, "Choose 5, 10, 15 or 20 clips.")
    if clipLength not in ("15-30", "30-45", "45-60", "60-90"): raise HTTPException(400, "Invalid clip duration.")
    if aspectRatio not in ("9:16", "1:1", "16:9"): raise HTTPException(400, "Invalid output format.")
    name = (file.filename or "").lower()
    if not name.endswith((".mp4", ".webm")): raise HTTPException(400, "Please upload an MP4 or WebM file.")
    jid = uuid.uuid4().hex
    folder = BASE / jid
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / ("source" + (".webm" if name.endswith(".webm") else ".mp4"))
    total = 0
    try:
        with source.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > 500 * 1024 * 1024:
                    out.close()
                    source.unlink(missing_ok=True)
                    raise HTTPException(413, "Video exceeds the 500 MB upload limit.")
                out.write(chunk)
    finally:
        await file.close()
    payload = {"videoUrl": "", "clipCount": clipCount, "clipLength": clipLength, "aspectRatio": aspectRatio}
    with lock: jobs[jid] = {"status":"queued","progress":0,"startedAt":time.time(),"message":"Upload complete. Waiting for the free worker…","clips":[]}
    threading.Thread(target=run_job, args=(jid, payload, source), daemon=True).start()
    return {"jobId": jid, "status":"processing"}

@app.get("/health")
def health(): return {"ok":True,"service":"melder-clip-worker"}

@app.post("/jobs")
def create_job(body: JobRequest, authorization: str = Header(default="")):
    auth(authorization)
    parsed = urlparse(str(body.videoUrl))
    if parsed.hostname and parsed.hostname.lower() in {"youtube.com","www.youtube.com","m.youtube.com","youtu.be","www.youtu.be"} and not body.rightsConfirmed:
        raise HTTPException(400, "Confirm that you own this video or have permission to clip it.")
    jid=uuid.uuid4().hex
    with lock: jobs[jid]={"status":"queued","progress":0,"startedAt":time.time(),"message":"Waiting for the free clip worker…","clips":[]}
    threading.Thread(target=run_job,args=(jid,body.model_dump(mode="json")),daemon=True).start()
    return {"jobId":jid,"status":"processing"}

@app.get("/jobs/{job_id}")
def get_job(job_id: str, authorization: str = Header(default="")):
    auth(authorization)
    with lock:
        if job_id not in jobs: raise HTTPException(404,"Job not found")
        data = dict(jobs[job_id])
        now = time.time()
        data["elapsedSeconds"] = max(0, int(now - data.get("startedAt", now)))
        if data.get("status") == "completed":
            data["etaSeconds"] = 0
        elif data.get("status") == "processing" and data.get("processingStartedAt") and data.get("progress", 0) >= 10:
            elapsed = max(1, now - data["processingStartedAt"])
            remaining = elapsed * (100 - data["progress"]) / max(1, data["progress"])
            data["etaSeconds"] = max(0, int(remaining))
        else:
            data["etaSeconds"] = None
        return data

@app.get("/jobs/{job_id}/clips/{number}")
def get_clip(job_id: str, number: int):
    path=BASE/job_id/f"clip-{number}.mp4"
    if not path.is_file(): raise HTTPException(404,"Clip not found or expired")
    return FileResponse(path,media_type="video/mp4",filename=f"melder-clip-{number}.mp4")

import os, uuid, time, threading, subprocess, tempfile, shutil, requests, hmac, hashlib, time as clock
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Melder Clip Worker")
app.add_middleware(CORSMiddleware, allow_origins=["https://melder-shorts-growth.vercel.app"], allow_credentials=False, allow_methods=["GET","POST","OPTIONS"], allow_headers=["Authorization","Content-Type"])
SECRET = os.getenv("CLIPPER_WORKER_SECRET", "")
BASE = Path(tempfile.gettempdir()) / "melder-jobs"
BASE.mkdir(exist_ok=True)
jobs = {}
lock = threading.Lock()

class JobRequest(BaseModel):
    videoUrl: HttpUrl
    clipCount: int = 5
    clipLength: str = "30-45"
    aspectRatio: str = "9:16"

def auth(value):
    if not SECRET or value != f"Bearer {SECRET}":
        raise HTTPException(401, "Unauthorized worker request")

def detect_highlights(source, duration, clip_length, count, folder):
    """Offline ranking from audio activity and scene changes; no external API."""
    import array, math, re
    rawfile = folder / "analysis-audio.pcm"
    subprocess.run(["ffmpeg","-y","-i",str(source),"-vn","-ac","1","-ar","8000","-f","s16le",str(rawfile)],
                   capture_output=True,check=True,timeout=900)
    raw = rawfile.read_bytes()
    rawfile.unlink(missing_ok=True)
    samples = array.array("h")
    samples.frombytes(raw[:len(raw)-(len(raw)%2)])
    per_second = []
    rate = 8000
    for pos in range(0, len(samples), rate):
        chunk = samples[pos:pos+rate]
        if chunk:
            rms = math.sqrt(sum(x*x for x in chunk)/len(chunk))
            per_second.append(20*math.log10(max(rms,1)/32768))
    if not per_second:
        raise ValueError("No usable audio track was found in this video.")
    scene = subprocess.run(["ffmpeg","-i",str(source),"-vf","select=gt(scene\\,0.22),showinfo","-an","-f","null","-"],
                           capture_output=True,text=True,timeout=900)
    scene_times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", scene.stderr)]
    baseline = sorted(per_second)[max(0,int(len(per_second)*0.15)-1)]
    candidates=[]
    step=max(5,clip_length//3)
    last=max(0,int(duration-clip_length))
    for start in range(0,last+1,step):
        end=min(duration,start+clip_length)
        a=max(0,int(start)); b=min(len(per_second),max(a+1,int(end)))
        vals=per_second[a:b]
        if not vals: continue
        active=sum(1 for v in vals if v>baseline+5)/len(vals)
        mean=sum(vals)/len(vals)
        variation=math.sqrt(sum((v-mean)**2 for v in vals)/len(vals))
        scenes=sum(1 for t in scene_times if start<=t<end)
        scene_rate=min(1.0,scenes/max(1,clip_length/8))
        score=max(0,min(100,round(45*active+30*min(1,variation/12)+25*scene_rate)))
        candidates.append({"start":start,"score":score})
    candidates.sort(key=lambda x:x["score"],reverse=True)
    chosen=[]
    min_gap=max(clip_length*0.65,20)
    for c in candidates:
        if all(abs(c["start"]-x["start"])>=min_gap for x in chosen):
            chosen.append(c)
        if len(chosen)>=count: break
    if not chosen:
        raise ValueError("Could not identify highlight sections. Try a video with clear audio.")
    return sorted(chosen,key=lambda x:x["start"])

def run_job(job_id, payload, uploaded_source=None):
    folder = BASE / job_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        url = str(payload["videoUrl"])
        if any(host in url.lower() for host in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
            raise ValueError("YouTube links are not downloaded by this worker. Use a video file you own or have permission to process, hosted at a direct MP4/WebM URL.")
        source = uploaded_source or (folder / "source.bin")
        if uploaded_source is None:
            with requests.get(url, stream=True, timeout=(20, 180), headers={"User-Agent":"MelderClipper/1.0"}) as response:
                response.raise_for_status()
                ctype = response.headers.get("content-type", "")
                if "text/html" in ctype.lower(): raise ValueError("That URL serves a webpage, not a video file. Use a direct video-file URL.")
                total = 0
                with source.open("wb") as out:
                    for chunk in response.iter_content(1024*1024):
                        if chunk:
                            total += len(chunk)
                            if total > 700*1024*1024: raise ValueError("Video exceeds the 700 MB processing limit.")
                            out.write(chunk)
        probe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(source)],capture_output=True,text=True,check=True)
        duration = float(probe.stdout.strip())
        if duration < 8: raise ValueError("Video is too short to clip.")
        low, high = [int(x) for x in payload["clipLength"].split("-")]
        length = min(high, max(low, 30))
        count = min(20, max(1, int(payload["clipCount"])))
        count = min(count, max(1, int(duration // max(8, length*0.65))))
        ratio = payload.get("aspectRatio", "9:16")
        vf = {"9:16":"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280", "1:1":"scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080", "16:9":"scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720"}.get(ratio, "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280")
        with lock: jobs[job_id].update(progress=8,message="Analysing audio and scene changes to find standout moments…")
        candidates=detect_highlights(source,duration,length,count,folder)
        clips=[]
        for i,candidate in enumerate(candidates):
            start=candidate["start"]
            out=folder/f"clip-{i+1}.mp4"
            subprocess.run(["ffmpeg","-y","-ss",str(start),"-i",str(source),"-t",str(min(length,duration-start)),"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","24","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)],capture_output=True,check=True,timeout=900)
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
    with lock: jobs[jid] = {"status":"processing","progress":0,"message":"Upload complete. Preparing your video","clips":[]}
    threading.Thread(target=run_job, args=(jid, payload, source), daemon=True).start()
    return {"jobId": jid, "status":"processing"}

@app.get("/health")
def health(): return {"ok":True,"service":"melder-clip-worker"}

@app.post("/jobs")
def create_job(body: JobRequest, authorization: str = Header(default="")):
    auth(authorization)
    jid=uuid.uuid4().hex
    with lock: jobs[jid]={"status":"processing","progress":0,"message":"Downloading and preparing your video","clips":[]}
    threading.Thread(target=run_job,args=(jid,body.model_dump(mode="json")),daemon=True).start()
    return {"jobId":jid,"status":"processing"}

@app.get("/jobs/{job_id}")
def get_job(job_id: str, authorization: str = Header(default="")):
    auth(authorization)
    with lock:
        if job_id not in jobs: raise HTTPException(404,"Job not found")
        return dict(jobs[job_id])

@app.get("/jobs/{job_id}/clips/{number}")
def get_clip(job_id: str, number: int):
    path=BASE/job_id/f"clip-{number}.mp4"
    if not path.is_file(): raise HTTPException(404,"Clip not found or expired")
    return FileResponse(path,media_type="video/mp4",filename=f"melder-clip-{number}.mp4")

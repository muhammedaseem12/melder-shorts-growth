import os, uuid, time, threading, subprocess, tempfile, shutil, requests
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Melder Clip Worker")
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

def run_job(job_id, payload):
    folder = BASE / job_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        url = str(payload["videoUrl"])
        if any(host in url.lower() for host in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
            raise ValueError("YouTube links are not downloaded by this worker. Use a video file you own or have permission to process, hosted at a direct MP4/WebM URL.")
        source = folder / "source.bin"
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
        clips=[]
        # Evenly spaced candidate excerpts; editorial/AI highlight ranking is not enabled in this baseline.
        for i in range(count):
            start = max(0, (duration-length) * (i/(max(1,count-1)))) if count > 1 else max(0,(duration-length)/2)
            out = folder / f"clip-{i+1}.mp4"
            subprocess.run(["ffmpeg","-y","-ss",str(start),"-i",str(source),"-t",str(min(length,duration-start)),"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","24","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)],capture_output=True,check=True,timeout=900)
            clips.append({"title":f"Clip {i+1}","hook":"Auto-extracted excerpt","duration":round(min(length,duration-start)),"score":None,"downloadUrl":f"/jobs/{job_id}/clips/{i+1}"})
            with lock: jobs[job_id].update(progress=int((i+1)/count*100), clips=clips)
        with lock: jobs[job_id].update(status="completed",progress=100,message="Clips are ready. These are evenly spaced excerpts, not AI-ranked highlights.",clips=clips)
    except Exception as e:
        with lock: jobs[job_id].update(status="failed",message=str(e),progress=0)

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

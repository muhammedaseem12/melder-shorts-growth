// Poll either a Ssemble YouTube job (24-char ID) or the self-hosted worker job.
module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "GET") return res.status(405).json({ error: "Method not allowed" });
  const jobId = req.query && req.query.jobId;
  if (!jobId || typeof jobId !== "string" || !/^[A-Za-z0-9_-]{6,128}$/.test(jobId)) return res.status(400).json({ error: "Invalid job ID." });

  if (/^[a-f0-9]{24}$/i.test(jobId)) {
    const key = process.env.SSEMBLE_API_KEY;
    if (!key) return res.status(503).json({ error: "YouTube clipping integration is not configured." });
    try {
      const headers = { "X-API-Key": key };
      const statusResponse = await fetch("https://aiclipping.ssemble.com/api/v1/shorts/" + jobId + "/status", { headers });
      const statusJson = await statusResponse.json().catch(() => ({}));
      if (!statusResponse.ok) return res.status(502).json({ error: statusJson.message || statusJson.error || "Could not check YouTube clipping status." });
      const s = statusJson.data || {};
      if (s.status === "failed" || s.status === "cancelled") return res.status(200).json({ status: "failed", progress: Number(s.progress)||0, message: s.stepMessage || "YouTube video processing failed.", error: s.failureReason || s.stepMessage || "Processing failed." });
      if (s.status !== "completed") return res.status(200).json({ status: "processing", progress: Math.max(0,Math.min(100,Number(s.progress)||0)), message: s.stepMessage || "AI is analysing your YouTube video…" , clips: [] });
      const clipsResponse = await fetch("https://aiclipping.ssemble.com/api/v1/shorts/" + jobId, { headers });
      const clipsJson = await clipsResponse.json().catch(() => ({}));
      if (!clipsResponse.ok) return res.status(502).json({ error: clipsJson.message || clipsJson.error || "Could not retrieve generated clips." });
      const items = Array.isArray(clipsJson.data?.shorts) ? clipsJson.data.shorts : [];
      const clips = items.filter(c => !c.error && typeof c.video_url === "string" && /^https:\/\//.test(c.video_url)).map(c => ({
        title: String(c.title || "Generated clip").slice(0,180),
        hook: String(c.reason || c.description || "").slice(0,280),
        duration: (Number(c.duration)||0) + " sec",
        score: Number.isFinite(Number(c.viral_score)) ? Math.max(0,Math.min(100,Number(c.viral_score))) : undefined,
        downloadUrl: c.video_url
      }));
      return res.status(200).json({ status: "completed", progress: 100, message: "Your YouTube clips are ready.", clips });
    } catch {
      return res.status(502).json({ error: "Could not reach the YouTube clipping provider." });
    }
  }

  const worker = process.env.CLIPPER_WORKER_URL;
  const secret = process.env.CLIPPER_WORKER_SECRET;
  if (!worker || !secret) return res.status(503).json({ error: "Video processing is not configured." });
  try {
    const response = await fetch(new URL("/jobs/" + encodeURIComponent(jobId), worker), { headers: { "Authorization": "Bearer " + secret } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return res.status(502).json({ error: data.error || "Could not fetch job status." });
    const allowed = new Set(["queued","processing","completed","failed"]);
    if (!allowed.has(data.status)) return res.status(502).json({ error: "Worker returned an unknown job status." });
    const clips = Array.isArray(data.clips) ? data.clips.slice(0,30).map(c => ({
      title: String(c.title || "").slice(0,180), hook: String(c.hook || "").slice(0,280),
      duration: String(c.duration || "").slice(0,24),
      score: Number.isFinite(Number(c.score)) ? Math.max(0,Math.min(100,Number(c.score))) : undefined,
      downloadUrl: typeof c.downloadUrl === "string" && c.downloadUrl.startsWith("/") ? new URL(c.downloadUrl, worker).toString() : (typeof c.downloadUrl === "string" && /^https:\/\//.test(c.downloadUrl) ? c.downloadUrl : undefined)
    })) : [];
    return res.status(200).json({ status:data.status, progress:Math.max(0,Math.min(100,Number(data.progress)||0)), message:String(data.message||"").slice(0,300), clips, error:data.status==="failed"?String(data.error||"Processing failed").slice(0,300):undefined });
  } catch {
    return res.status(502).json({ error: "Could not reach the video processing worker." });
  }
};
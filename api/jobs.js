// Start a clipping job. YouTube URLs use Ssemble's official clipping API;
// direct MP4/WebM URLs continue to use the self-hosted FFmpeg worker.
function youtubeUrl(value) {
  try {
    const u = new URL(value);
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    return u.protocol === "https:" && (host === "youtube.com" || host === "m.youtube.com" || host === "youtu.be");
  } catch { return false; }
}
function validDirectUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "https:" && !["youtube.com","www.youtube.com","m.youtube.com","youtu.be","www.youtu.be"].includes(u.hostname.toLowerCase());
  } catch { return false; }
}
module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });
  const body = req.body || {};
  const url = String(body.videoUrl || "").trim();
  const clipCount = Number(body.clipCount);
  const lengths = new Set(["15-30","30-45","45-60","60-90"]);
  const aspects = new Set(["9:16","1:1","16:9"]);
  if (![5,10,15,20].includes(clipCount)) return res.status(400).json({ error: "Choose 5, 10, 15 or 20 clips." });
  if (!lengths.has(body.clipLength) || !aspects.has(body.aspectRatio)) return res.status(400).json({ error: "Invalid output settings." });

  if (youtubeUrl(url)) {
    const key = process.env.SSEMBLE_API_KEY;
    if (!key) return res.status(503).json({ error: "YouTube clipping integration is prepared, but not activated yet. Add your Ssemble API key in Vercel as SSEMBLE_API_KEY. The provider requires an active account and credits." });
    try {
      const preferredLength = body.clipLength === "15-30" ? "under30sec" : body.clipLength === "30-45" ? "under60sec" : body.clipLength === "45-60" ? "under60sec" : "under90sec";
      const response = await fetch("https://aiclipping.ssemble.com/api/v1/shorts/create", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": key },
        body: JSON.stringify({
          url, start: 0, end: 1200, preferredLength, language: "en",
          layout: body.aspectRatio === "1:1" ? "square" : body.aspectRatio === "16:9" ? "fit" : "auto",
          music: false
        })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) return res.status(response.status === 401 || response.status === 403 ? 502 : 400).json({ error: result.message || result.error || result.data?.message || "YouTube clipping provider rejected this link. Check that the video is public and not a YouTube Short, and that your provider account has credits." });
      const id = result.data?.requestId || result.requestId || result.data?._id;
      if (!id) return res.status(502).json({ error: "The YouTube clipping provider returned no request ID." });
      return res.status(202).json({ jobId: id, status: "processing", provider: "ssemble" });
    } catch {
      return res.status(502).json({ error: "Could not reach the YouTube clipping provider. Please try again." });
    }
  }

  if (!validDirectUrl(url)) return res.status(400).json({ error: "Paste a valid HTTPS YouTube video link or a direct MP4/WebM video-file URL." });
  const worker = process.env.CLIPPER_WORKER_URL;
  const secret = process.env.CLIPPER_WORKER_SECRET;
  if (!worker || !secret) return res.status(503).json({ error: "Video processing is not configured." });
  try {
    const response = await fetch(new URL("/jobs", worker), {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + secret },
      body: JSON.stringify({ videoUrl: url, clipCount, clipLength: body.clipLength, aspectRatio: body.aspectRatio })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return res.status(502).json({ error: data.error || "The video worker could not accept this job." });
    if (!data.jobId) return res.status(502).json({ error: "The video worker returned an invalid job response." });
    return res.status(202).json({ jobId: data.jobId, status: data.status || "queued", provider: "worker" });
  } catch {
    return res.status(502).json({ error: "Could not reach the video processing worker." });
  }
};
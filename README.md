# MELDER AI CLIPPER

A creator-focused long-video-to-Shorts application. Paste a YouTube URL, choose the number and length of clips, and submit a job to a private AI processing worker. The interface supports job progress and a gallery of generated clip downloads.

## Current repository status

- Professional responsive clipping workspace in `index.html`.
- YouTube URL validation for watch, Shorts, youtu.be, live and embed URLs.
- Configurable clip count, duration and aspect ratio.
- Secure Vercel serverless API proxy: `api/jobs.js` and `api/jobs/[jobId].js`.
- Worker credentials are read only from server-side environment variables and never sent to the browser.
- Graceful setup message when the worker is not connected.

## Important: processing worker required

The repository does not yet contain the video-processing worker. A YouTube URL alone is not a downloadable video file, and the browser cannot safely or reliably extract a full YouTube video due to platform restrictions, CORS and copyright controls. The app therefore delegates processing to a private worker that you control and are authorised to use.

Configure these Vercel environment variables:

- `CLIPPER_WORKER_URL`: HTTPS base URL of your processing worker, e.g. `https://your-worker.example.com` (no trailing path required).
- `CLIPPER_WORKER_SECRET`: long random secret shared with the worker. Never expose it in frontend variables.

### Worker API contract

The app sends:

`POST {CLIPPER_WORKER_URL}/jobs`

Headers: `Authorization: Bearer <CLIPPER_WORKER_SECRET>`, `Content-Type: application/json`

Body:
```json
{
  "videoUrl": "https://www.youtube.com/watch?v=VIDEO_ID",
  "clipCount": 10,
  "clipLength": "30-45",
  "aspectRatio": "9:16"
}
```

Expected response: `{ "jobId": "job_123", "status": "queued" }`

The app polls:

`GET {CLIPPER_WORKER_URL}/jobs/{jobId}`

Expected response:
```json
{
  "status": "processing",
  "progress": 45,
  "message": "Finding high-retention moments",
  "clips": []
}
```

When completed, return `status: "completed"` and clips such as:
```json
{
  "status": "completed",
  "progress": 100,
  "clips": [
    {
      "title": "The surprising answer",
      "hook": "A short description of the moment",
      "duration": "00:32",
      "score": 91,
      "downloadUrl": "https://your-worker.example.com/exports/clip-1.mp4"
    }
  ]
}
```

The worker is responsible for authorised source ingestion, transcription, highlight scoring, context-safe cut selection, speaker-aware 9:16 reframing, caption rendering, FFmpeg export, temporary storage and expiring download links. A sensible implementation can use FFmpeg, a speech-to-text provider, an LLM for transcript segment scoring, and object storage. Do not expose provider keys to the browser.

## Deploy

1. Import this repository into Vercel.
2. Deploy the static frontend and serverless API.
3. Add the two worker environment variables above only after deploying a compatible worker.
4. Redeploy after changing environment variables.

Without the worker variables, the app intentionally returns a clear setup message instead of pretending that clips were generated.

## Rights and platform compliance

Only process videos you own or have permission to use. Respect YouTube's terms, copyright, privacy, and the rights of people shown or heard. Do not use bots, fake engagement, autoplay loops or view exchanges. AI can help identify engaging moments; it cannot guarantee virality, views or audience retention.

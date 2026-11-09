# MELDER YT Shorts Growth Studio

A mobile-friendly starter site for validating and previewing YouTube Shorts links, then sharing them with genuinely interested audiences.

## Current features
- Accepts YouTube Shorts, standard watch, and youtu.be URLs.
- Validates the 11-character video ID.
- Embeds a privacy-enhanced YouTube preview.
- Opens and copies the canonical Shorts URL.
- Responsive dark/purple/cyan MELDER YT design.

## Important limitations
This starter is a front-end only. It does **not** generate views, automate playback, refresh videos, exchange views, or promise a view count. A link preview does not submit a video to a shared public promotion feed. Organic views happen only when real people choose to watch.

## Deploy on Vercel
1. Import this GitHub repository into Vercel.
2. Framework preset: Other.
3. Build command: leave blank.
4. Output directory: `.`
5. Deploy.

GitHub Pages can also host this static `index.html`.

## Next backend phase
To support a genuine shared community feed, configure a Supabase project and apply `SUPABASE_SCHEMA.sql`. Add the Supabase project URL and anon key to a frontend configuration file. For private channel analytics, implement Google OAuth and the YouTube Analytics API; never put a Google client secret or service-role key in browser code.

## YouTube policy
Do not add bots, autoplay loops, forced redirects, view exchanges, paid-to-watch tasks, or artificial traffic. These can violate YouTube's fake engagement rules and may put a channel at risk.

-- MELDER YT: optional backend schema for a real community discovery feed.
-- Apply in a fresh Supabase project. This does not create or inflate views.
create extension if not exists pgcrypto;
create table if not exists public.short_submissions (
 id uuid primary key default gen_random_uuid(),
 video_id text not null check (video_id ~ '^[A-Za-z0-9_-]{11}$'),
 video_url text not null,
 creator_name text,
 topic text,
 description text,
 created_at timestamptz not null default now(),
 status text not null default 'pending' check (status in ('pending','published','hidden'))
);
alter table public.short_submissions enable row level security;
create policy "Public can view published Shorts"
on public.short_submissions for select using (status = 'published');
create policy "Anyone can submit a Short for review"
on public.short_submissions for insert with check (status = 'pending');
-- No public update/delete policies. Moderation belongs on a trusted admin/server.
-- Never expose a Supabase service_role key in browser code.

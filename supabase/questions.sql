-- Optional external question bank for ConsensusKill.
--
-- Run this in the Supabase SQL editor (free tier is fine), then load the
-- bundled bank with:
--   SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/seed_supabase.py
--
-- Column names must match shared/questions.py -> load_supabase_questions(),
-- which selects: id, text, option_a, option_b.

create table if not exists public.questions (
  id        text primary key,
  text      text not null,
  option_a  text not null,
  option_b  text not null
);

-- The game only ever reads the bank; writes happen via the seed script
-- (service role key, which bypasses RLS).
alter table public.questions enable row level security;

drop policy if exists "questions are publicly readable" on public.questions;
create policy "questions are publicly readable"
  on public.questions
  for select
  using (true);

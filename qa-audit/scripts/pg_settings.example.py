"""Test settings for the audit: a local throwaway PostgreSQL and nothing else.

Copy this file to a folder OUTSIDE the repository as pg_settings.py, put that folder on PYTHONPATH and pass --settings=pg_settings.
It removes every variable that could point Django at Supabase or at the live website before the real settings load, so a test
run cannot reach production even if such variables are set in the shell."""
import os

os.environ.setdefault('DJANGO_SECRET_KEY','audit-only-not-secret')
os.environ['DATABASE_PASSWORD']='x'                 # the throwaway cluster uses trust authentication
os.environ['DATABASE_HOST']='127.0.0.1'
os.environ['DATABASE_PORT']='55432'
for name in ('DATABASE_URL','SUPABASE_URL','SUPABASE_INTERNAL_URL','SUPABASE_SECRET_KEY','SUPABASE_PUBLISHABLE_KEY','VERCEL','WEBSITE_REVALIDATE_URL'):
    os.environ.pop(name,None)

from config.settings import *       # noqa: E402,F401,F403

"""Run the pinned Supabase stack with the Covison overlay and a fixed project name."""
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
if not (ROOT/'server/.env').exists():raise SystemExit('Run python3 supabase/setup_local.py --host SERVER_IP first.')
env=dict(line.split('=',1) for line in (ROOT/'server/.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
from main_route import enabled
command=['docker','compose','--project-directory',str(ROOT/'server'),'--env-file',str(ROOT/'server/.env'),
    '-f',str(ROOT/'server/docker-compose.yml'),'-f',str(ROOT/'compose.covison.yaml')]
if enabled(env):command+=['-f',str(ROOT/'compose.main.yaml')]
command+=['-p','covison-supabase']
raise SystemExit(subprocess.call(command+sys.argv[1:],cwd=ROOT/'server'))

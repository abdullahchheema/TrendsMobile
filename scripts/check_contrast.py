"""Check WCAG contrast of the design tokens in offline/static/css/tokens.css. Exit 1 if any required pair fails.
Run:  python scripts/check_contrast.py"""
import re,sys
from pathlib import Path
css=(Path(__file__).resolve().parent.parent/'offline/static/css/tokens.css').read_text(encoding='utf-8')
tok=dict(re.findall(r'--([a-z0-9-]+):(#[0-9a-fA-F]{6})\b',css))
def lum(h):
    c=[int(h[i:i+2],16)/255 for i in (1,3,5)]
    c=[x/12.92 if x<=.03928 else ((x+.055)/1.055)**2.4 for x in c]
    return .2126*c[0]+.7152*c[1]+.0722*c[2]
def ratio(a,b):
    la,lb=lum(tok[a] if a in tok else a),lum(tok[b] if b in tok else b)
    hi,lo=max(la,lb),min(la,lb);return (hi+.05)/(lo+.05)
TEXT=4.5;UI=3.0
checks=[]
for fg in ('ink','ink-2','ink-3','accent-text','ok','warn','bad','info'):
    for bg in ('bg','surface','surface-2','surface-3'):checks.append((fg,bg,TEXT))
checks+=[('accent-ink','accent',TEXT),('accent-ink','accent-hover',TEXT),('ring','surface',UI),('ring','bg',UI)]
bad=0
for fg,bg,need in checks:
    r=ratio(fg,bg);ok=r>=need;bad+=not ok
    print(('PASS' if ok else 'FAIL'),f'{fg:12s} on {bg:10s} {r:5.2f}:1 (need {need})')
sys.exit(1 if bad else 0)

(()=>{
 'use strict';
 const status=document.querySelector('[data-server-status]');if(!status)return;
 let sequence=0;let reachable=null;
 async function check(){
  const ticket=++sequence;const started=performance.now();const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),4000);let online=false;
  try{const response=await fetch('/health/',{cache:'no-store',redirect:'error',signal:controller.signal});online=response.ok&&(await response.json()).status==='ok';}catch{}finally{clearTimeout(timeout);}
  if(ticket!==sequence)return;
  reachable=online;status.dataset.reachable=String(online);
  status.textContent=online?'Server reachable · '+Math.round(performance.now()-started)+' ms':'Server unreachable. Check the internet connection; keep this page open.';
  status.classList.toggle('error',!online);
  document.dispatchEvent(new CustomEvent('covison-server-status',{detail:{reachable:online}}));
 }
 window.covisonConnection={check,get reachable(){return reachable;}};
 const button=document.querySelector('[data-check-server]');
 const onVisible=()=>{if(!document.hidden)check();};
 button?.addEventListener('click',check);
 window.addEventListener('online',check);window.addEventListener('offline',check);
 document.addEventListener('visibilitychange',onVisible);
 const timer=setInterval(()=>{if(!document.hidden)check();},20000);check();
 /* The page is swapped in place now, so stop polling and detach when it is left. */
 window.covison?.page?.leave(()=>{
  sequence++;clearInterval(timer);
  window.removeEventListener('online',check);window.removeEventListener('offline',check);
  document.removeEventListener('visibilitychange',onVisible);
  delete window.covisonConnection;
 });
})();

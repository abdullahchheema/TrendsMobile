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
  status.textContent=online?'Local server reachable · '+Math.round(performance.now()-started)+' ms':'Local server unavailable. Check the LAN and server; keep this page open.';
  status.classList.toggle('error',!online);
  document.dispatchEvent(new CustomEvent('covison-server-status',{detail:{reachable:online}}));
 }
 window.covisonConnection={check,get reachable(){return reachable;}};
 document.querySelector('[data-check-server]')?.addEventListener('click',check);
 window.addEventListener('online',check);window.addEventListener('offline',check);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
 setInterval(()=>{if(!document.hidden)check();},20000);check();
})();

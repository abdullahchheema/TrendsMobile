(()=>{
 'use strict';
 const field=document.getElementById('scanner-check');const result=document.getElementById('scanner-result');
 document.getElementById('browser-details').textContent='Browser platform: '+(navigator.platform||'Unknown')+' · Viewport: '+innerWidth+' × '+innerHeight;
 field.addEventListener('keydown',event=>{
  if(event.key!=='Enter')return;event.preventDefault();const value=field.value.trim();
  if(!value){result.textContent='No input received.';return;}
  result.textContent=value+' · Stock reference / barcode input and Enter suffix received. Compare it with the printed label.';
 });
 document.getElementById('clear-scanner-check').addEventListener('click',()=>{field.value='';result.textContent='Waiting for a scan followed by Enter.';field.focus();});
})();

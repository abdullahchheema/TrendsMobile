(()=>{
 'use strict';
 const field=document.getElementById('scanner-check');const result=document.getElementById('scanner-result');
 document.getElementById('browser-details').textContent='Browser platform: '+(navigator.platform||'Unknown')+' · Viewport: '+innerWidth+' × '+innerHeight;
 field.addEventListener('keydown',event=>{
  if(event.key!=='Enter')return;event.preventDefault();const value=field.value.trim();
  if(!value){result.textContent='No input received.';return;}
  if(/^[0-9]{15}$/.test(value)){
   const total=[...value].reduce((sum,c,i)=>{const n=Number(c)*(i%2?2:1);return sum+Math.floor(n/10)+n%10;},0);
   result.textContent=value+' · Enter suffix received · '+(total%10===0?'IMEI format/check digit valid. PTA status is not checked.':'IMEI check digit is invalid; compare the label and scanner settings.');
  }else result.textContent=value+' · Barcode input and Enter suffix received. Compare it with the printed label.';
 });
 document.getElementById('clear-scanner-check').addEventListener('click',()=>{field.value='';result.textContent='Waiting for a scan followed by Enter.';field.focus();});
})();

(()=>{
 'use strict';
 const modes=JSON.parse(document.getElementById('cash-payment-modes')?.textContent||'[]');
 const format=cents=>new Intl.NumberFormat('en-PK',{style:'currency',currency:'PKR',currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(cents/100);
 function cents(value){
  const text=String(value).trim();if(!/^\d+(?:\.\d{0,2})?$/.test(text))return null;
  const [whole,fraction='']=text.split('.');const amount=Number(whole)*100+Number(fraction.padEnd(2,'0'));
  return Number.isSafeInteger(amount)?amount:null;
 }
 const rows=form=>[...form.querySelectorAll('[data-payment-row]')];
 function refresh(form){
  let applied=0,handed=0,change=0,valid=true;
  for(const row of rows(form)){
   const cash=modes.includes(row.querySelector('[name=payment_mode]').value),amount=row.querySelector('[name=payment_amount]'),tender=row.querySelector('[name=payment_tendered]');
   const section=row.querySelector('[data-cash-input]');section.hidden=!cash;
   tender.disabled=!cash||form.dataset.paymentLocked==='true';if(!cash)tender.value='';
   row.querySelector('[name=payment_cash_value]').value=cash?tender.value:'';
   tender.setCustomValidity('');
   const net=cents(amount.value||'0'),gross=cash?cents(tender.value||amount.value||'0'):0;
   let message='';
   if(cash&&tender.value&&(net===null||net<=0))message='Enter a positive amount to apply to the invoice.';
   else if(cash&&gross===null)message='Use cash amounts with at most two decimal places.';
   else if(cash&&net!==null&&gross<net)message='Cash handed cannot be less than the amount applied.';
   tender.setCustomValidity(message);
   row.querySelector('[data-cash-change]').textContent=message||format(cash&&net!==null?gross-net:0);
   if(net===null||message)valid=false;
   else{applied+=net;if(cash){handed+=gross;change+=gross-net;}}
  }
  const summary=form.querySelector('[data-payment-summary]');
  if(summary)summary.textContent=valid?'Applied '+format(applied)+' · Cash handed '+format(handed)+' · Change '+format(change):'Check the payment amounts before posting.';
  return {applied,handed,change,valid};
 }
 function read(form){
  const state=refresh(form);if(!state.valid)throw Error('Check the cash handed and applied amounts.');
  const result=[];
  for(const row of rows(form)){
   const mode=row.querySelector('[name=payment_mode]').value,amount=row.querySelector('[name=payment_amount]').value,tender=row.querySelector('[name=payment_tendered]').value,reference=(row.querySelector('[name=payment_reference]')||{}).value||'';
   if((!amount||cents(amount)===0)&&!tender)continue;
   if(cents(amount)===null||cents(amount)<=0)throw Error('Enter a positive applied amount for each payment.');
   const payment={mode,amount,reference};if(modes.includes(mode)&&tender)payment.cash_tendered=tender;
   result.push(payment);
  }
  return result;
 }
 document.addEventListener('input',event=>{if(event.target.closest('[data-payment-rows]'))refresh(event.target.form);});
 document.addEventListener('change',event=>{if(event.target.closest('[data-payment-rows]'))refresh(event.target.form);});
 document.addEventListener('submit',event=>{if(event.target.querySelector('[data-payment-rows]')&&!refresh(event.target).valid){event.preventDefault();event.target.reportValidity();}});
 document.addEventListener('click',event=>{
  const remove=event.target.closest('[data-remove-payment]');if(!remove)return;
  const form=remove.form;if(form.dataset.paymentLocked==='true')return;
  const all=rows(form);if(all.length>1)remove.closest('[data-payment-row]').remove();else all[0].querySelectorAll('input').forEach(input=>{input.value='';});refresh(form);
 });
 window.covisonPayments={cents,format,read,refresh};
 document.querySelectorAll('form').forEach(form=>{if(form.querySelector('[data-payment-rows]'))refresh(form);});
})();

/* Small shared behaviours. Everything is delegated from `document` (or re-applied by ready()) so it keeps working
   after a page is swapped in place; this file is loaded once in <head>. */
(()=>{
 'use strict';
 const ready=window.covison?.page?.ready||(fn=>fn());

 /* Filter dropdowns remember what the address bar says. */
 ready(()=>{
  const active=new URLSearchParams(window.location.search);
  document.querySelectorAll('.cov-filters select').forEach(select=>{if(active.has(select.name))select.value=active.get(select.name);});
 });

 /* Scanner/Enter on an IMEI box moves to the next field instead of submitting. */
 document.addEventListener('keydown',event=>{
  const field=event.target;
  if(event.key!=='Enter'||!field.matches||!field.matches('input[name="imei_1"],input[name="imei_2"]'))return;
  event.preventDefault();
  const fields=[...field.form.elements].filter(e=>!e.disabled&&e.type!=='hidden'&&e.tagName!=='BUTTON');
  const next=fields[fields.indexOf(field)+1];next?.focus();
 });

 document.addEventListener('click',e=>{
  if(e.target.closest('[data-print]'))window.print();
  if(e.target.closest('[data-add-line]')){const total=document.getElementById('id_lines-TOTAL_FORMS');const template=document.getElementById('empty-line');document.querySelector('#line-items tbody').insertAdjacentHTML('beforeend',template.innerHTML.replaceAll('__prefix__',total.value));total.value=Number(total.value)+1;}
  if(e.target.closest('[data-add-payment]')){const form=e.target.closest('form');if(form.dataset.paymentLocked==='true')return;const body=form.querySelector('[data-payment-rows]');const row=body.firstElementChild.cloneNode(true);row.querySelectorAll('input').forEach(i=>i.value='');body.append(row);window.covisonPayments?.refresh(form);}
  if(e.target.closest('[data-add-method]')){const total=document.getElementById('id_methods-TOTAL_FORMS');const template=document.getElementById('empty-method');document.querySelector('[data-method-rows]').insertAdjacentHTML('beforeend',template.innerHTML.replaceAll('__prefix__',total.value));total.value=Number(total.value)+1;}

  /* Phone menu (the drawer): open with the button, close by tapping the page. */
  const toggle=e.target.closest('[data-menu-toggle]'),nav=document.getElementById('main-navigation');
  if(toggle&&nav){const open=nav.classList.toggle('is-open');toggle.setAttribute('aria-expanded',String(open));}
  else if(e.target.closest('.app-main'))closeMenu();
 });
 function closeMenu(){
  document.getElementById('main-navigation')?.classList.remove('is-open');
  document.querySelector('[data-menu-toggle]')?.setAttribute('aria-expanded','false');
 }
 document.addEventListener('keydown',event=>{if(event.key==='Escape')closeMenu();});
})();

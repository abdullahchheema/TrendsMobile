(()=>{
 'use strict';
 const root=document.getElementById('pos');if(!root)return;
 const products=JSON.parse(document.getElementById('pos-data').textContent),cart=[];
 const rules=JSON.parse(document.getElementById('pos-tax-rules').textContent),customers=JSON.parse(document.getElementById('pos-customers').textContent);
 const list=document.getElementById('pos-products'),cartEl=document.getElementById('cart-items'),form=document.getElementById('pos-cart');
 const search=document.getElementById('pos-search'),error=document.getElementById('pos-error'),submit=form.querySelector('button.pos-complete');
 const allShops=root.dataset.allShops==='true',shopBox=document.getElementById('pos-shop'),fromEl=document.getElementById('pos-from');
 const nameBox=document.getElementById('pos-customer-name'),phoneBox=document.getElementById('pos-customer-phone');
 const brandBox=document.getElementById('pos-brand'),modelBox=document.getElementById('pos-model'),storageBox=document.getElementById('pos-storage');
 let sending=false,pending=null,uncertain=false,over='';
 const format=value=>new Intl.NumberFormat('en-PK',{style:'currency',currency:'PKR',currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
 const make=(tag,text,cls)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(cls)element.className=cls;return element;};
 const csrf=()=>decodeURIComponent(document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.slice(10)||form.querySelector('[name=csrfmiddlewaretoken]').value);
 const priced=product=>Number(product.rate)>0;

 /* ---------- order totals (same rules as the server) ---------- */
 function adjustments(){const rule=rules.find(item=>item.id===form.elements.tax_rule.value);return {discount_type:form.elements.discount_type.value,discount_value:form.elements.discount_value.value||'0',tax_rule:rule?.id||'',tax_version:rule?.version||''};}
 function totals(){const spec=adjustments();return window.covisonPricing.calculate(cart,spec,rules.find(rule=>rule.id===spec.tax_rule));}
 function received(){return [...form.querySelectorAll('[name=payment_amount]')].reduce((sum,input)=>sum+(Number(input.value)||0),0);}
 function money(){
  let total=0;try{total=Number(totals().total);}catch{total=0;}
  const paid=received();document.getElementById('pos-received').textContent=format(paid);
  if(paid>total+0.004&&total>=0&&cart.length){over='The amount received ('+format(paid)+') is more than the sale total ('+format(total)+'). Enter only what the sale is for; money handed over beyond that goes in “Cash handed over” so the change is worked out.';error.textContent=over;}
  else if(over){if(error.textContent===over)error.textContent='';over='';}
  const creditBox=document.getElementById('pos-credit'),credit=creditBox?Number(creditBox.dataset.credit)||0:0;
  const afterPay=Math.max(0,Math.round((total-paid)*100)/100),used=Math.min(credit,afterPay);
  const remaining=Math.max(0,Math.round((afterPay-used)*100)/100);document.getElementById('pos-remaining').textContent=format(remaining)+(used>0?' (after '+format(used)+' return credit)':'');
  document.querySelector('.pos-total-row.remaining').classList.toggle('due',remaining>0&&cart.length>0);
 }
 function updateButton(){submit.disabled=sending||window.covisonConnection?.reachable!==true;submit.textContent=sending?'Posting…':pending?'Retry same sale':'Complete sale';}
 function lockEditor(){const locked=Boolean(pending);form.dataset.paymentLocked=String(locked);for(const element of form.elements)if(element!==submit&&element.type!=='hidden')element.disabled=locked;search.disabled=locked;list.querySelectorAll('button').forEach(button=>button.disabled=locked);window.covisonPayments.refresh(form);render();updateButton();}

 /* ---------- the order ---------- */
 function add(product){
  if(pending)return;
  if(allShops&&cart.length&&cart[0].branch!==product.branch){error.textContent='One sale can include phones from one shop only ('+cart[0].shop+'). Finish or clear this sale first.';return;}
  const existing=cart.find(row=>row.product===product.product&&row.device===product.device&&row.warehouse===product.warehouse);
  if(existing){
   if(product.serialized){error.textContent='This exact phone is already in the sale.';return;}
   if(product.stock_item&&existing.quantity>=Number(product.stock)){error.textContent='The available quantity is already in the sale.';return;}
   existing.quantity++;
  }else cart.push({...product,quantity:1,list:product.rate});
  error.textContent='';render();
 }
 function render(){
  cartEl.replaceChildren();document.getElementById('pos-empty').hidden=cart.length>0;
  if(fromEl){fromEl.hidden=!(allShops&&cart.length);fromEl.textContent=cart.length?'Selling from: '+cart[0].shop:'';}
  cart.forEach((product,index)=>{
   const row=make('div',undefined,'cart-row');
   const head=make('div',undefined,'cart-head');head.append(make('strong',product.name));
   const remove=make('button','Remove');remove.type='button';remove.className='cart-remove';remove.disabled=Boolean(pending);remove.addEventListener('click',()=>{if(pending)return;cart.splice(index,1);error.textContent='';render();});head.append(remove);row.append(head);
   if(product.imeis&&product.imeis.length)row.append(make('small','IMEI '+product.imeis.join(' / '),'cart-imei'));
   const controls=make('div',undefined,'cart-controls');
   const rate=make('input');rate.type='number';rate.min='0';rate.step='.01';rate.value=priced(product)?product.rate:'';rate.placeholder='Selling price';rate.disabled=Boolean(pending);rate.setAttribute('aria-label','Selling price PKR');rate.className='cart-rate';
   rate.addEventListener('change',()=>{if(pending)return;product.rate=rate.value;render();});
   if(product.serialized){controls.append(make('span','1 phone','cart-qty-static'));}
   else{const qty=make('input');qty.type='number';qty.min='1';qty.step='1';qty.value=product.quantity;qty.disabled=Boolean(pending);qty.setAttribute('aria-label','Quantity');qty.className='cart-qty';
    if(product.stock_item)qty.max=product.stock;
    qty.addEventListener('change',()=>{if(pending)return;product.quantity=Number(qty.value);render();});controls.append(make('label','Qty'),qty);}
   controls.append(make('label','Price (PKR)'),rate);row.append(controls);
   const listed=Number(product.list||0),now=Number(product.rate||0);
   if(priced(product)&&listed>0&&now<listed)row.append(make('small','List price '+format(listed)+' · discount '+format(listed-now)+' each','cart-discount'));
   if(priced(product)&&product.cost!=null&&product.cost!==''&&now<=Number(product.cost))row.append(make('small','Selling price is at or below the purchase price ('+format(Number(product.cost))+'). Check it before selling. If this phone came in a bulk purchase, its recorded cost may be a shared average.','cart-warn'));
   if(product.serialized){const label=make('label','Warranty','cart-warranty'),sel=make('select');sel.required=true;sel.disabled=Boolean(pending);for(const [value,text] of [['','Choose warranty'],['0','No warranty'],['7','7 days'],['15','15 days'],['30','1 month'],['90','3 months'],['180','6 months'],['365','1 year']]){const o=make('option',text);o.value=value;sel.append(o);}sel.value=product.warranty||'';sel.addEventListener('change',()=>{if(!pending)product.warranty=sel.value;});label.append(sel);row.append(label);}
   cartEl.append(row);
  });
  try{syncFinal();const values=totals();document.getElementById('cart-total').textContent=format(values.total);document.getElementById('cart-words').textContent=Number(values.total)>0&&window.covison?.words?window.covison.words(values.total):'';document.getElementById('cart-breakdown').textContent=cart.length?`Subtotal ${format(values.subtotal)} − discount ${format(values.discount)} + tax ${format(values.tax)}`:'';}
  catch(problem){document.getElementById('cart-total').textContent='Check invoice pricing';document.getElementById('cart-breakdown').textContent=problem.message;}
  money();
 }

 /* ---------- quick preview (the eye on a card) ---------- */
 const dialog=document.getElementById('pos-preview'),previewBody=document.getElementById('pos-preview-body');let previewing=null;
 function preview(product){
  previewing=product;previewBody.replaceChildren();
  previewBody.append(make('h2',product.serialized?[product.brand,product.model].filter(Boolean).join(' ')||product.name:product.name));
  const rows=[['Shop',allShops?product.shop:''],['Storage',product.storage],['Colour',product.colour],['PTA status',product.pta_status&&product.pta_status!=='Not recorded'?product.pta_status:''],['Type',product.kind],['Condition',product.condition+(product.grade?' · grade '+product.grade+'/10':'')],
   ['Battery health',product.battery?product.battery+'%':''],['IMEI',(product.imeis||[]).join(' / ')],['Stock reference',product.code],['In stock since',product.received],['Note',product.notes],
   ['Selling price',priced(product)?format(product.rate):'Not priced yet — set it at the sale'],['Purchase price (cost)',product.cost!=null&&product.cost!==''?format(Number(product.cost)):'']];
  const table=make('dl',undefined,'pos-facts');
  for(const [label,value] of rows){if(value===''||value===null||value===undefined)continue;table.append(make('dt',label),make('dd',String(value)));}
  previewBody.append(table);dialog.showModal();
 }
 if(dialog){
  dialog.querySelector('[data-preview-close]').addEventListener('click',()=>dialog.close());
  dialog.querySelector('[data-preview-add]').addEventListener('click',()=>{if(previewing)add(previewing);dialog.close();search.focus();});
  dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
  window.covison?.page?.leave(()=>{if(dialog.open)dialog.close();});
 }

 /* ---------- the product grid ---------- */
 const unique=(key,filter)=>[...new Set(products.filter(filter||(()=>true)).map(p=>p[key]).filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));
 function fill(select,values,keep){const current=keep?select.value:'';[...select.options].slice(1).forEach(o=>o.remove());values.forEach(v=>{const o=make('option',v);o.value=v;select.append(o);});if(values.includes(current))select.value=current;}
 function filters(){fill(brandBox,unique('brand'),true);fill(modelBox,unique('model',p=>!brandBox.value||p.brand===brandBox.value),true);fill(storageBox,unique('storage',p=>(!brandBox.value||p.brand===brandBox.value)&&(!modelBox.value||p.model===modelBox.value)),true);}
 function show(){
  list.replaceChildren();const terms=search.value.toLowerCase().split(/\s+/).filter(Boolean);
  const condition=document.getElementById('pos-condition')?.value||'',maxPrice=Number(document.getElementById('pos-max')?.value||0);
  const found=products.filter(p=>{const hay=`${p.code} ${p.name} ${p.barcode} ${p.brand||''} ${p.model||''} ${p.storage||''} ${p.colour||''} ${p.condition||''}`.toLowerCase();
   return terms.every(t=>hay.includes(t))&&(!shopBox||!shopBox.value||p.shop===shopBox.value)&&(!brandBox.value||p.brand===brandBox.value)&&(!modelBox.value||p.model===modelBox.value)&&(!storageBox.value||p.storage===storageBox.value)&&(!condition||p.condition===condition)&&(!maxPrice||!priced(p)||Number(p.rate)<=maxPrice);});
  document.getElementById('pos-count').textContent=`${found.length} item${found.length===1?'':'s'} available${found.length>120?' (showing the first 120 — narrow the search)':''}`;
  if(!found.length)list.append(make('p','Nothing in stock matches. Check the filters or scan the IMEI.','empty-state'));
  found.slice(0,120).forEach(product=>{
   const card=make('div',undefined,'pos-card');
   const pick=make('button');pick.type='button';pick.disabled=Boolean(pending);pick.className='pos-card-pick';
   const initial=make('span',(product.brand||product.name||'?').trim().charAt(0).toUpperCase(),'pos-avatar');initial.setAttribute('aria-hidden','true');
   const body=make('span',undefined,'pos-card-body');
   if(allShops)body.append(make('small','Shop: '+product.shop,'pos-shop-tag'));
   const title=product.serialized?[product.brand,product.model].filter(Boolean).join(' ')||product.name:product.name;
   body.append(make('strong',title));
   const facts=product.serialized?[product.storage,product.colour,product.pta_status&&product.pta_status!=='Not recorded'?product.pta_status:'',product.battery?'Battery '+product.battery+'%':'',product.condition,product.kind]:[product.category,product.colour];
   body.append(make('small',facts.filter(Boolean).join(' · ')));
   const foot=make('span',undefined,'pos-card-foot');foot.append(make('b',priced(product)?format(product.rate):'Price at sale'));
   foot.append(make('em',product.serialized?'In stock':(product.stock_item?`${product.stock} in stock`:'Service'),'pos-avail'));
   pick.append(initial,body,foot);pick.addEventListener('click',()=>{add(product);search.focus();});
   const eye=make('button');eye.type='button';eye.className='pos-eye';eye.setAttribute('aria-label','Quick preview of '+title);eye.title='Quick preview';
   eye.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
   eye.addEventListener('click',event=>{event.stopPropagation();preview(product);});
   card.append(pick,eye);list.append(card);
  });
 }

 /* ---------- customer ---------- */
 const suggestions=document.getElementById('pos-customer-suggestions');customers.forEach(c=>{const o=document.createElement('option');o.value=c.name;suggestions.append(o);});
 function pickCustomer(){const hit=customers.find(c=>c.name.toLowerCase()===nameBox.value.trim().toLowerCase());if(hit){phoneBox.value=hit.phone||phoneBox.value;phoneBox.dataset.known=hit.phone?'1':'';}}
 nameBox.addEventListener('input',pickCustomer);nameBox.addEventListener('change',pickCustomer);

 /* ---------- events ---------- */
 let finalDriven=false;const finalBox=document.getElementById('pos-final');
 function syncFinal(){
  if(!finalBox||finalBox.value.trim()==='')return;
  const base=window.covisonPricing.calculate(cart,{discount_type:'amount',discount_value:'0'},null),subtotal=Number(base.subtotal),want=Number(finalBox.value);
  if(!(want>=0)||want>subtotal){error.textContent='The final total cannot be more than the items add up to ('+format(subtotal)+'). Change an item price instead.';return;}
  error.textContent='';form.elements.discount_type.value='amount';form.elements.discount_value.value=(Math.round((subtotal-want)*100)/100).toFixed(2);finalDriven=true;
 }
 if(finalBox)finalBox.addEventListener('input',()=>{if(finalBox.value.trim()===''&&finalDriven){form.elements.discount_value.value='0';finalDriven=false;}render();});
 for(const name of ['discount_type','tax_rule'])form.elements[name].addEventListener('change',()=>{if(finalBox)finalBox.value='';finalDriven=false;render();});
 form.elements.discount_value.addEventListener('input',()=>{if(finalBox)finalBox.value='';finalDriven=false;render();});
 form.addEventListener('input',money);form.addEventListener('change',money);
 for(const box of [brandBox,modelBox,storageBox])box.addEventListener('change',()=>{if(box===brandBox)modelBox.value='';if(box!==storageBox)storageBox.value='';filters();show();});
 for(const id of ['pos-condition','pos-max'])document.getElementById(id)?.addEventListener('input',show);
 search.addEventListener('input',show);
 search.addEventListener('keydown',event=>{
  if(event.key!=='Enter'||pending)return;event.preventDefault();const value=search.value.trim();if(!value)return;
  const matches=products.filter(p=>p.code===value||(p.barcodes||p.barcode.split(/\s+/)).includes(value));
  if(matches.length===1){add(matches[0]);search.value='';show();search.focus();}
  else error.textContent=matches.length?'More than one item has this code. Choose the right one from the list.':'No exact stock reference or IMEI match. Choose an item from the search results.';
 });
 document.addEventListener('covison-server-status',updateButton);
 const guard=event=>{if(pending){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',guard);
 /* Pages now swap in place: ask before leaving mid-sale, and detach our document/window listeners when we do leave. */
 if(window.covison?.page){
  window.covison.page.guard=()=>pending?window.confirm('A sale is still being posted. If you leave now you will not see its result. Leave anyway?'):true;
  window.covison.page.leave(()=>{document.removeEventListener('covison-server-status',updateButton);window.removeEventListener('beforeunload',guard);});
 }

 /* ---------- finishing the sale ---------- */
 function whatsapp(result){
  const digits=(phoneBox.value||'').replace(/\D/g,'');const number=digits?(digits.startsWith('92')?digits:digits.startsWith('0')?'92'+digits.slice(1):'92'+digits):'';
  const text=`Thank you for shopping at ${result.shop}.\nReceipt ${result.code}\nTotal: PKR ${Number(result.total).toLocaleString('en-PK')}${Number(result.outstanding)>0?`\nBalance due: PKR ${Number(result.outstanding).toLocaleString('en-PK')}`:''}`;
  return `https://wa.me/${number}?text=${encodeURIComponent(text)}`;
 }
 function finished(result){
  pending=null;window.removeEventListener('beforeunload',guard);form.hidden=true;const done=document.getElementById('pos-done');done.hidden=false;
  document.getElementById('pos-done-text').textContent=`${result.code} · total ${format(result.total)}${Number(result.outstanding)>0?' · balance due '+format(result.outstanding):' · paid in full'}.`;
  document.getElementById('pos-print').href=result.receipt+'?paper=80&print=1';document.getElementById('pos-share').href=whatsapp(result);document.getElementById('pos-view').href=result.url;
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(sending)return;
  if(window.covisonConnection?.reachable!==true){error.textContent='Reconnect to the server before posting.';return;}
  if(!pending){
   if(!cart.length){error.textContent='Select at least one item.';return;}
   if(!form.reportValidity())return;
   const unpriced=cart.find(item=>!(Number(item.rate)>0));if(unpriced){error.textContent='Enter the selling price for '+unpriced.name+'.';return;}
   const noWarranty=cart.find(item=>item.serialized&&(item.warranty===undefined||item.warranty===''));if(noWarranty){error.textContent='Choose the warranty for '+noWarranty.name+' (or No warranty).';return;}
   try{totals();}catch(problem){error.textContent=problem.message;return;}
   const values=new FormData(form);
   let payments;try{payments=window.covisonPayments.read(form);}catch(problem){error.textContent=problem.message;return;}
   if(received()>Number(totals().total)+0.004){error.textContent='The amount received ('+format(received())+') is more than the sale total ('+format(Number(totals().total))+'). Correct the amount before completing the sale.';return;}
   if(payments.some(p=>Number(p.amount)<=0)){error.textContent='Payment amounts must be more than zero.';return;}
   pending={request_key:root.dataset.requestKey,branch:(cart[0]&&cart[0].branch)||root.dataset.branch,customer_name:values.get('customer_name'),customer_phone:values.get('customer_phone')||'',seller:values.get('seller'),handler:values.get('handler'),items:cart.map(({product,device,warehouse,quantity,rate,warranty})=>({product,device,warehouse,quantity,rate,warranty_days:warranty===undefined?'':warranty})),credit_doc:(document.getElementById('pos-credit')||{dataset:{}}).dataset.creditDoc||'',adjustments:adjustments(),override:{approver:values.get('override_approver')||'',pin:values.get('override_pin')||''},credit:{due_date:values.get('credit_due')||'',instalments:values.get('credit_instalments')||'0'},payments};
  }
  sending=true;error.textContent='';lockEditor();
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);
  try{
   const response=await fetch(root.dataset.endpoint,{method:'POST',redirect:'error',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(pending)});
   const result=await response.json();
   if(response.ok&&result.id){finished(result);return;}
   if(response.status===400&&result.error&&!uncertain){pending=null;error.textContent=result.error;return;}
   throw new Error('Unknown sale result');
  }catch{
   uncertain=true;error.textContent='The sale result is uncertain. Keep this page open. Reconnect (or sign in in another tab), then retry this same sale. Check recent invoices before starting another sale.';
  }finally{clearTimeout(timeout);sending=false;if(!form.hidden)lockEditor();}
 });
 if(shopBox){[...new Set(products.map(p=>p.shop))].sort().forEach(name=>{const o=make('option',name);o.value=name;shopBox.append(o);});shopBox.addEventListener('change',show);}
 filters();show();render();updateButton();search.focus();
 if(root.dataset.addDevice){const wanted=products.find(item=>item.device===root.dataset.addDevice);if(wanted)add(wanted);}
})();

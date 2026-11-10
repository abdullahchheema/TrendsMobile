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
 let sending=false,pending=null,uncertain=false;
 const format=value=>new Intl.NumberFormat('en-PK',{style:'currency',currency:'PKR',currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
 const make=(tag,text,cls)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(cls)element.className=cls;return element;};
 const csrf=()=>decodeURIComponent(document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.slice(10)||form.querySelector('[name=csrfmiddlewaretoken]').value);
 const priced=product=>Number(product.rate)>0;

 /* ---------- order totals (same rules as the server) ---------- */
 function adjustments(){const rule=rules.find(item=>item.id===form.elements.tax_rule.value);return {discount_type:form.elements.discount_type.value,discount_value:form.elements.discount_value.value||'0',tax_rule:rule?.id||'',tax_version:rule?.version||''};}
 function totals(){const spec=adjustments();return window.covisonPricing.calculate(cart,spec,rules.find(rule=>rule.id===spec.tax_rule));}
 function received(){return [...form.querySelectorAll('[name=payment_amount]')].reduce((sum,input)=>sum+(Number(input.value)||0),0);}
 function money(){
  let total=0;try{total=totals().total;}catch{total=0;}
  const paid=received();document.getElementById('pos-received').textContent=format(paid);
  const remaining=Math.max(0,Math.round((total-paid)*100)/100);document.getElementById('pos-remaining').textContent=format(remaining);
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
  }else cart.push({...product,quantity:1});
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
   const qty=make('input');qty.type='number';qty.min='1';qty.step='1';qty.value=product.quantity;qty.disabled=product.serialized||Boolean(pending);qty.setAttribute('aria-label','Quantity');
   if(product.stock_item)qty.max=product.stock;
   qty.addEventListener('change',()=>{if(pending)return;product.quantity=Number(qty.value);render();});
   const rate=make('input');rate.type='number';rate.min='0';rate.step='.01';rate.value=priced(product)?product.rate:'';rate.placeholder='Selling price';rate.disabled=Boolean(pending);rate.setAttribute('aria-label','Selling price PKR');
   rate.addEventListener('change',()=>{if(pending)return;product.rate=rate.value;render();});
   controls.append(make('label','Qty'),qty,make('label','Price'),rate);row.append(controls);
   if(product.serialized){const label=make('label','Warranty','cart-warranty'),sel=make('select');sel.required=true;sel.disabled=Boolean(pending);for(const [value,text] of [['','Choose warranty'],['0','No warranty'],['7','7 days'],['15','15 days'],['30','1 month'],['90','3 months'],['180','6 months'],['365','1 year']]){const o=make('option',text);o.value=value;sel.append(o);}sel.value=product.warranty||'';sel.addEventListener('change',()=>{if(!pending)product.warranty=sel.value;});label.append(sel);row.append(label);}
   cartEl.append(row);
  });
  try{const values=totals();document.getElementById('cart-total').textContent=format(values.total);document.getElementById('cart-breakdown').textContent=cart.length?`Subtotal ${format(values.subtotal)} − discount ${format(values.discount)} + tax ${format(values.tax)}`:'';}
  catch(problem){document.getElementById('cart-total').textContent='Check invoice pricing';document.getElementById('cart-breakdown').textContent=problem.message;}
  money();
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
   const button=make('button');button.type='button';button.disabled=Boolean(pending);button.className='pos-card';
   const initial=make('span',(product.brand||product.name||'?').trim().charAt(0).toUpperCase(),'pos-avatar');initial.setAttribute('aria-hidden','true');
   const body=make('span',undefined,'pos-card-body');
   if(allShops)body.append(make('small','Shop: '+product.shop,'pos-shop-tag'));
   body.append(make('strong',product.name),make('small',[product.storage,product.colour,product.condition,product.grade?product.grade+'/10':''].filter(Boolean).join(' · ')));
   if(product.imeis&&product.imeis.length)body.append(make('small','IMEI '+product.imeis.join(' / ')));
   const foot=make('span',undefined,'pos-card-foot');foot.append(make('b',priced(product)?format(product.rate):'Price at sale'));
   foot.append(make('em',product.serialized?'In stock':(product.stock_item?`${product.stock} in stock`:'Service'),'pos-avail'));
   button.append(initial,body,foot);button.addEventListener('click',()=>{add(product);search.focus();});list.append(button);
  });
 }

 /* ---------- customer ---------- */
 const suggestions=document.getElementById('pos-customer-suggestions');customers.forEach(c=>{const o=document.createElement('option');o.value=c.name;suggestions.append(o);});
 function pickCustomer(){const hit=customers.find(c=>c.name.toLowerCase()===nameBox.value.trim().toLowerCase());if(hit){phoneBox.value=hit.phone||phoneBox.value;phoneBox.dataset.known=hit.phone?'1':'';}}
 nameBox.addEventListener('input',pickCustomer);nameBox.addEventListener('change',pickCustomer);

 /* ---------- events ---------- */
 for(const name of ['discount_type','discount_value','tax_rule'])form.elements[name].addEventListener('change',render);
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
   pending={request_key:root.dataset.requestKey,branch:(cart[0]&&cart[0].branch)||root.dataset.branch,customer_name:values.get('customer_name'),customer_phone:values.get('customer_phone')||'',seller:values.get('seller'),handler:values.get('handler'),items:cart.map(({product,device,warehouse,quantity,rate,warranty})=>({product,device,warehouse,quantity,rate,warranty_days:warranty===undefined?'':warranty})),adjustments:adjustments(),override:{approver:values.get('override_approver')||'',pin:values.get('override_pin')||''},credit:{due_date:values.get('credit_due')||'',instalments:values.get('credit_instalments')||'0'},payments};
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

(()=>{
 'use strict';
 const root=document.getElementById('pos');if(!root)return;
 const products=JSON.parse(document.getElementById('pos-data').textContent),cart=[];
 const rules=JSON.parse(document.getElementById('pos-tax-rules').textContent);
 const list=document.getElementById('pos-products'),cartEl=document.getElementById('cart-items'),form=document.getElementById('pos-cart');
 const search=document.getElementById('pos-search'),error=document.getElementById('pos-error'),submit=form.querySelector('button.primary');
 const hasShift=root.dataset.shiftOpen==='true';let sending=false,pending=null,uncertain=false;
 const format=value=>new Intl.NumberFormat('en-PK',{style:'currency',currency:'PKR',currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
 const make=(tag,text)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;return element;};
 const csrf=()=>decodeURIComponent(document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.slice(10)||form.querySelector('[name=csrfmiddlewaretoken]').value);
 function adjustments(){const rule=rules.find(item=>item.id===form.elements.tax_rule.value);return {discount_type:form.elements.discount_type.value,discount_value:form.elements.discount_value.value||'0',tax_rule:rule?.id||'',tax_version:rule?.version||''};}
 function totals(){const spec=adjustments();return window.covisonPricing.calculate(cart,spec,rules.find(rule=>rule.id===spec.tax_rule));}
 function updateButton(){submit.disabled=sending||!hasShift||window.covisonConnection?.reachable!==true;submit.textContent=sending?'Posting…':pending?'Retry same sale':'Complete sale';}
 function lockEditor(){const locked=Boolean(pending);form.dataset.paymentLocked=String(locked);for(const element of form.elements)if(element!==submit&&element.type!=='hidden')element.disabled=locked;search.disabled=locked;list.querySelectorAll('button').forEach(button=>button.disabled=locked);window.covisonPayments.refresh(form);render();updateButton();}
 function add(product){
  if(pending)return;
  const existing=cart.find(row=>row.product===product.product&&row.device===product.device&&row.warehouse===product.warehouse);
  if(existing){
   if(product.serialized){error.textContent='This exact phone is already in the sale.';return;}
   if(product.stock_item&&existing.quantity>=Number(product.stock)){error.textContent='The available quantity is already in the sale.';return;}
   existing.quantity++;
  }else cart.push({...product,quantity:1});
  error.textContent='';render();
 }
 function show(){
  list.replaceChildren();const query=search.value.toLowerCase();
  products.filter(p=>`${p.code} ${p.name} ${p.barcode}`.toLowerCase().includes(query)).slice(0,80).forEach(product=>{
   const button=make('button');button.type='button';button.disabled=Boolean(pending);
   button.append(make('span',product.name),make('strong',format(product.rate)),make('small',product.code),make('small',product.warehouse_name||''));
   if(product.serialized)button.append(make('small','PTA: '+product.pta_status));else if(product.stock_item)button.append(make('small','In stock: '+product.stock));
   button.addEventListener('click',()=>add(product));list.append(button);
  });
 }
 function render(){
  cartEl.replaceChildren();
  cart.forEach((product,index)=>{
   const row=make('div');row.className='cart-row';row.append(make('strong',product.name));
   const qty=make('input');qty.type='number';qty.min='1';qty.step='1';qty.value=product.quantity;qty.disabled=product.serialized||Boolean(pending);qty.setAttribute('aria-label','Quantity');
   if(product.stock_item)qty.max=product.stock;
   qty.addEventListener('change',()=>{if(pending)return;product.quantity=Number(qty.value);render();});
   const rate=make('input');rate.type='number';rate.min='0';rate.step='.01';rate.value=product.rate;rate.disabled=Boolean(pending);rate.setAttribute('aria-label','Selling price PKR');
   rate.addEventListener('change',()=>{if(pending)return;product.rate=rate.value;render();});
   const remove=make('button','Remove');remove.type='button';remove.disabled=Boolean(pending);remove.addEventListener('click',()=>{if(pending)return;cart.splice(index,1);error.textContent='';render();});
   row.append(qty,rate,remove);cartEl.append(row);
   if(product.serialized){const label=make('label','PTA status'),pta=make('select');pta.required=true;pta.disabled=Boolean(pending);for(const [value,text] of [['','Choose PTA status'],['PTA','PTA'],['Non-PTA','Non-PTA']]){const option=make('option',text);option.value=value;pta.append(option);}pta.value=['PTA','Non-PTA'].includes(product.pta_status)?product.pta_status:'';pta.addEventListener('change',()=>{if(!pending)product.pta_status=pta.value;});label.append(pta);row.append(label);}
  });
  try{const values=totals();document.getElementById('cart-total').textContent=format(values.total);document.getElementById('cart-breakdown').textContent=`Subtotal ${format(values.subtotal)} − discount ${format(values.discount)} + tax ${format(values.tax)}`;}
  catch(problem){document.getElementById('cart-total').textContent='Check invoice pricing';document.getElementById('cart-breakdown').textContent=problem.message;}
 }
 for(const name of ['discount_type','discount_value','tax_rule'])form.elements[name].addEventListener('change',render);
 search.addEventListener('input',show);
 search.addEventListener('keydown',event=>{
  if(event.key!=='Enter'||pending)return;event.preventDefault();const value=search.value.trim();if(!value)return;
  const matches=products.filter(p=>p.code===value||(p.barcodes||p.barcode.split(/\s+/)).includes(value));
  if(matches.length===1){add(matches[0]);search.value='';show();}
  else error.textContent=matches.length?'This barcode has multiple warehouse matches. Choose the correct item and warehouse.':'No exact stock reference or barcode match. Choose an item from the search results.';
 });
 document.addEventListener('covison-server-status',updateButton);
 window.addEventListener('beforeunload',event=>{if(pending){event.preventDefault();event.returnValue='';}});
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(sending)return;
  if(window.covisonConnection?.reachable!==true){error.textContent='Reconnect to the server before posting.';return;}
  if(!hasShift){error.textContent='Start your POS shift first.';return;}
  if(!pending){
   if(!cart.length){error.textContent='Select at least one item.';return;}
   if(!form.reportValidity())return;
   try{totals();}catch(problem){error.textContent=problem.message;return;}
   const values=new FormData(form);
   let payments;try{payments=window.covisonPayments.read(form);}catch(problem){error.textContent=problem.message;return;}
   pending={request_key:root.dataset.requestKey,branch:root.dataset.branch,customer_name:values.get('customer_name'),seller:values.get('seller'),handler:values.get('handler'),items:cart.map(({product,device,warehouse,quantity,rate,pta_status})=>({product,device,warehouse,quantity,rate,pta_status})),adjustments:adjustments(),payments};
  }
  sending=true;error.textContent='';lockEditor();
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);
  try{
   const response=await fetch(root.dataset.endpoint,{method:'POST',redirect:'error',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(pending)});
   const result=await response.json();
   if(response.ok&&result.id&&result.url){pending=null;window.location.assign(result.url);return;}
   if(response.status===400&&result.error&&!uncertain){pending=null;error.textContent=result.error;return;}
   throw new Error('Unknown sale result');
  }catch{
   uncertain=true;error.textContent='The sale result is uncertain. Keep this page open. Reconnect (or sign in in another tab), then retry this same sale. Check recent invoices before starting another sale.';
  }finally{clearTimeout(timeout);sending=false;lockEditor();}
 });
 show();render();updateButton();
})();

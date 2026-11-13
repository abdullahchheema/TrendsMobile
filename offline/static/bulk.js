/* Buy many phones: one card per phone (details, price, IMEI 1 and optional IMEI 2), scanner friendly, live totals.
   Each phone is sent to the server as a row of quantity 1; the server re-validates everything. */
(function(){
  'use strict';
  var form=document.querySelector('[data-bulk]');if(!form)return;
  var conditions=JSON.parse(document.getElementById('bulk-conditions').textContent),types=JSON.parse(document.getElementById('bulk-types').textContent);
  var body=form.querySelector('[data-rows]'),hidden=form.elements.rows_json,status=form.querySelector('[data-status]'),totalEl=form.querySelector('[data-total]'),countEl=form.querySelector('[data-count]');
  var totalBox=form.querySelector('[data-bulk-total]'),shareEl=form.querySelector('[data-share]'),bulkInput=form.elements.bulk_total;
  var priceRows=form.querySelector('[data-price-rows]'),priceEach=form.querySelector('[data-price-each]'),sameBox=form.querySelector('[data-same]');
  var payStatus=form.elements.payment_status,payAmount=form.elements.amount_paid,payHint=form.querySelector('[data-pay-hint]'),payProblem='';
  var rows=[],lastFocus=null;
  function luhn(n){if(!/^\d{15}$/.test(n))return false;var s=0;for(var i=0;i<15;i++){var d=+n[i];if(i%2){d*=2;if(d>9)d-=9}s+=d}return s%10===0}
  function esc(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}
  function money(v){return 'PKR '+v.toLocaleString('en-PK',{minimumFractionDigits:2,maximumFractionDigits:2})}
  function bulkMode(){var r=form.querySelector('input[name=price_mode]:checked');return !!r&&r.value==='bulk'}
  function blank(src){return {brand:src?src.brand:'',model:src?src.model:'',storage:src?src.storage:'',colour:src?src.colour:'',notes:src?src.notes:'',quantity:1,condition:src?src.condition:'Good',device_type:src?src.device_type:'Used',grade:src?src.grade:'',pta_status:src?src.pta_status:'PTA',cost:src?src.cost:'',cost_mode:'each',asking_price:src?src.asking_price:'',imei1:'',imei2:''}}
  function opts(list,sel){return list.map(function(v){return '<option'+(v===sel?' selected':'')+'>'+esc(v)+'</option>'}).join('')}
  function field(label,html,cls){return '<label class="bf '+(cls||'')+'"><span>'+label+'</span>'+html+'</label>'}
  function cardHtml(r,i){
    var cat=window.PhoneCatalog||{models:function(){return []},storage:[]};
    return '<header><strong>Phone '+(i+1)+'</strong><span class="bulk-card-actions"><button type="button" data-dup title="Add another phone with the same details">Copy</button><button type="button" data-del aria-label="Remove phone '+(i+1)+'" title="Remove this phone">&#10005; Remove</button></span></header>'+
      '<div class="bulk-grid">'+
      field('Brand','<input data-f="brand" list="phone-brands" value="'+esc(r.brand)+'" placeholder="e.g. Samsung" autocomplete="off">')+
      field('Model','<input data-f="model" list="models-'+i+'" value="'+esc(r.model)+'" placeholder="e.g. Galaxy S21" autocomplete="off"><datalist id="models-'+i+'">'+cat.models(r.brand).map(function(m){return '<option value="'+esc(m)+'">'}).join('')+'</datalist>')+
      field('Storage','<select data-f="storage"><option value="">Choose</option>'+opts(cat.storage,r.storage)+'</select>')+
      field('Colour','<input data-f="colour" list="phone-colours" value="'+esc(r.colour)+'" placeholder="e.g. Black" autocomplete="off">')+
      field('PTA status','<select data-f="pta_status"><option'+(r.pta_status==='PTA'?' selected':'')+'>PTA</option><option'+(r.pta_status==='Non-PTA'?' selected':'')+'>Non-PTA</option><option'+(r.pta_status==='JV'?' selected':'')+'>JV</option></select>')+
      field('Condition','<select data-f="condition">'+opts(conditions,r.condition)+'</select>')+
      field('Type','<select data-f="device_type">'+opts(types,r.device_type)+'</select>')+
      field('Selling price (PKR)','<input data-f="asking_price" type="number" inputmode="decimal" min="0" step="0.01" data-words value="'+esc(r.asking_price)+'" placeholder="Price you will sell it at">','bf-price')+
      field('IMEI 1 &mdash; scan here','<input data-f="imei1" inputmode="numeric" autocomplete="off" maxlength="17" value="'+esc(r.imei1)+'" placeholder="15 digits">','bf-imei')+
      field('IMEI 2 (dual SIM, optional)','<input data-f="imei2" inputmode="numeric" autocomplete="off" maxlength="17" value="'+esc(r.imei2)+'" placeholder="15 digits">','bf-imei')+
      field('Note (optional)','<input data-f="notes" maxlength="400" value="'+esc(r.notes)+'" placeholder="battery changed, display replaced…" autocomplete="off">','bf-wide')+
      '</div><small class="bulk-warn" data-warn role="alert"></small>';
  }
  function phoneLabel(r,i){return 'Phone '+(i+1)+' · '+([r.brand,r.model,r.storage,r.colour].filter(Boolean).join(' ')||'details not entered yet')}
  /* Step 3: one price box per phone, or one total for all of them. Built from the phones above. */
  function renderPrices(){
    if(!priceRows)return;
    var each=!bulkMode();if(priceEach)priceEach.hidden=!each;if(totalBox)totalBox.hidden=each;
    if(priceRows.children.length!==rows.length){
      priceRows.innerHTML='';
      rows.forEach(function(r,i){var tr=document.createElement('tr');tr.dataset.i=i;tr.innerHTML='<td data-label></td><td><input data-p type="number" inputmode="decimal" min="0" step="0.01" placeholder="Price PKR" aria-label="Buying price"></td>';priceRows.appendChild(tr)});
    }
    rows.forEach(function(r,i){
      var tr=priceRows.children[i],inp=tr.querySelector('[data-p]');tr.querySelector('[data-label]').textContent=phoneLabel(r,i);
      if(document.activeElement!==inp){inp.value=r.cost==null?'':r.cost}
      if(window.covison&&window.covison.showWords){var tag=inp.nextElementSibling;if(!tag||!tag.classList.contains('words-line'))window.covison.showWords(inp);else window.covison.showWords(inp)}
    });
  }
  /* Step 4: the payment must agree with the total. Typing an amount means a part payment; the choice and the amount can never disagree. */
  function syncPay(total,typing){
    if(!payStatus||!payAmount)return;
    if(typing&&payStatus.value!=='part'&&payAmount.value!=='')payStatus.value='part';
    var mode=payStatus.value,box=payAmount.closest('.field');
    if(mode==='full')payAmount.value=total?total.toFixed(2):'';
    if(mode==='unpaid')payAmount.value='';
    if(box)box.hidden=mode!=='part';
    var paid=mode==='full'?total:mode==='part'?(+payAmount.value||0):0;payProblem='';
    if(mode==='part'){
      if(payAmount.value!==''&&paid<0)payProblem='The amount paid cannot be negative.';
      else if(paid>total)payProblem='You cannot pay '+money(paid)+' against a total of only '+money(total)+'. The most you can pay is '+money(total)+'.';
      else if(total>0&&(paid<=0||paid===total))payProblem=paid===total&&paid>0?'That is the whole total. Choose “Paid in full” instead.':'Enter how much is paid now (more than zero, less than the total).';
    }
    if(payHint){
      var words=window.covison&&window.covison.words?window.covison.words:null;
      payHint.textContent=payProblem||('Total '+money(total)+' · Paid now '+money(paid)+' · Still owed to supplier '+money(Math.max(0,total-paid)));
      payHint.className=payProblem?'error':'muted';
      if(!payProblem&&words&&total>0)payHint.textContent+=' — '+words(total.toFixed(2));
      var bal=(function(){try{return JSON.parse(document.getElementById('pay-balances').textContent)}catch(e){return {}}})(),mth=form.elements.method&&form.elements.method.value;
      if(!payProblem&&paid>0&&mth&&bal[mth]!==undefined&&paid>+bal[mth])payHint.textContent+=' ⚠ The books show only '+money(+bal[mth])+' in '+mth+', so this payment makes it negative. Record the opening cash first (Money In → Money put in by the owner).';
    }
  }
  function render(focusIndex,focusField){
    body.innerHTML='';
    rows.forEach(function(r,i){var card=document.createElement('section');card.className='phone-card';card.dataset.i=i;card.innerHTML=cardHtml(r,i);body.appendChild(card)});
    renderPrices();summarise();
    if(focusIndex!=null){var el=body.querySelector('.phone-card[data-i="'+focusIndex+'"] [data-f="'+(focusField||'imei1')+'"]');if(el){el.focus();if(el.scrollIntoView)el.scrollIntoView({block:'center'})}}
  }
  function currentTotal(){return bulkMode()?(+(bulkInput&&bulkInput.value)||0):rows.reduce(function(s,r){return s+(+r.cost||0)},0)}
  function summarise(){
    var all={},dup=false,bad=false,total=0,bulk=bulkMode(),n=rows.length;
    rows.forEach(function(r,i){
      var msgs=[];
      ['imei1','imei2'].forEach(function(k){
        var v=String(r[k]||'').replace(/\D/g,'');if(!v)return;
        if(!luhn(v))msgs.push('IMEI '+(k==='imei1'?'1':'2')+' is not a valid 15-digit IMEI');
        else if(all[v]){msgs.push('IMEI '+v+' is entered twice');dup=true}
        all[v]=1;
      });
      if(msgs.length)bad=true;
      var card=body.children[i],w=card&&card.querySelector('[data-warn]');if(w)w.textContent=msgs.join(' · ');
      total+=bulk?0:(+r.cost||0);
    });
    if(bulk){total=+(bulkInput&&bulkInput.value)||0;if(shareEl)shareEl.textContent=n&&total?'Shared equally: about '+money(total/n)+' per phone.':''}
    totalEl.textContent=money(total);countEl.textContent=n+' phone'+(n===1?'':'s');syncPay(total,false);
    hidden.value=JSON.stringify(rows.map(function(r){
      var imeis=[r.imei1,r.imei2].map(function(v){return String(v||'').replace(/\D/g,'')}).filter(Boolean).join(' ');
      return {brand:r.brand,model:r.model,storage:r.storage,colour:r.colour,notes:r.notes,quantity:1,condition:r.condition,device_type:r.device_type,grade:r.grade,pta_status:r.pta_status,cost:r.cost,cost_mode:'each',asking_price:r.asking_price,imeis:imeis};
    }));
    return {dup:dup,bad:bad};
  }
  body.addEventListener('input',function(e){
    var card=e.target.closest('.phone-card'),f=e.target.dataset.f;if(!card||!f)return;
    rows[+card.dataset.i][f]=e.target.value;
    if(f==='brand'){var dl=card.querySelector('datalist'),cat=window.PhoneCatalog;if(dl&&cat)dl.innerHTML=cat.models(e.target.value).map(function(m){return '<option value="'+esc(m)+'">'}).join('')}
    renderPrices();summarise();
  });
  /* scanners send the digits and then Enter: IMEI 1 -> IMEI 2 -> next phone (a new copy is added after the last one) */
  body.addEventListener('keydown',function(e){
    if(e.key!=='Enter')return;var f=e.target.dataset&&e.target.dataset.f;if(!f)return;
    var card=e.target.closest('.phone-card');if(!card)return;var i=+card.dataset.i;
    if(f==='imei1'||f==='imei2'){
      e.preventDefault();var v=String(e.target.value||'').replace(/\D/g,'');
      if(v&&!luhn(v)){status.textContent='“'+v+'” is not a valid IMEI. Check the number or scan again.';status.className='error';return}
      status.textContent='';status.className='muted';
      if(f==='imei1'&&v){var next=card.querySelector('[data-f="imei2"]');if(next)next.focus();return}
      if(f==='imei1'&&!v)return;
      if(i===rows.length-1){rows.push(blank(rows[i]));render(rows.length-1,'imei1')}
      else{var nx=body.querySelector('.phone-card[data-i="'+(i+1)+'"] [data-f="imei1"]');if(nx)nx.focus()}
    }else if(e.target.tagName==='INPUT'){e.preventDefault()}      /* Enter in a detail box must not submit the whole purchase */
  });
  if(priceRows)priceRows.addEventListener('input',function(e){var tr=e.target.closest('tr');if(!tr||!e.target.matches('[data-p]'))return;rows[+tr.dataset.i].cost=e.target.value;summarise()});
  var fillAll=form.querySelector('[data-apply-same]');
  if(fillAll)fillAll.addEventListener('click',function(){var v=sameBox&&sameBox.value;if(v===''||+v<=0)return;rows.forEach(function(r){r.cost=v});renderPrices();summarise()});
  if(payStatus)payStatus.addEventListener('change',function(){summarise()});
  if(form.elements.method)form.elements.method.addEventListener('change',function(){summarise()});
  if(payAmount)payAmount.addEventListener('input',function(){syncPay(currentTotal(),true);summarise()});
  body.addEventListener('click',function(e){
    var card=e.target.closest('.phone-card');if(!card)return;var i=+card.dataset.i;
    if(e.target.closest('[data-del]')){rows.splice(i,1);if(!rows.length)rows.push(blank());render(Math.min(i,rows.length-1),'brand')}
    else if(e.target.closest('[data-dup]')){rows.splice(i+1,0,blank(rows[i]));render(i+1,'imei1')}
  });
  form.querySelector('[data-add]').addEventListener('click',function(){rows.push(blank(rows[rows.length-1]));render(rows.length-1,rows.length>1?'imei1':'brand')});
  form.addEventListener('change',function(e){
    if(e.target.name==='price_mode'){renderPrices();summarise()}
  });
  if(bulkInput)bulkInput.addEventListener('input',summarise);
  form.addEventListener('submit',function(e){
    var res=summarise();var later=form.elements.later&&form.elements.later.checked;
    function stop(msg){e.preventDefault();status.textContent=msg;status.className='error';var c=body.querySelector('.phone-card.has-problem');}
    for(var i=0;i<rows.length;i++){
      var r=rows[i],miss=!(r.brand&&r.model&&r.storage&&r.colour);
      if(miss){stop('Phone '+(i+1)+': enter the brand, model, storage and colour.');render(i,'brand');return}
      if(!bulkMode()&&!(+r.cost>0)){stop('Phone '+(i+1)+': enter its buying price in step 3.');if(priceRows&&priceRows.children[i]){var pi=priceRows.children[i].querySelector('[data-p]');if(pi){pi.focus();pi.scrollIntoView({block:'center'})}}return}
      if(!(+r.asking_price>0)){stop('Phone '+(i+1)+': enter its selling price.');render(i,'asking_price');return}
      if(!later&&!String(r.imei1||'').replace(/\D/g,'')){stop('Phone '+(i+1)+': scan or type its IMEI (or tick “I will add IMEIs later”).');render(i,'imei1');return}
    }
    if(res.dup||res.bad){stop('Fix the IMEI problems shown on the phones before saving.');return}
    if(payProblem){stop(payProblem);if(payAmount)payAmount.focus()}
  },true);
  try{var saved=JSON.parse(hidden.value||'[]');
    if(Array.isArray(saved)&&saved.length){
      /* a rejected save comes back with its phones: one card per phone, quantities above 1 become several cards */
      saved.forEach(function(r){var n=Math.max(1,Math.min(100,+r.quantity||1)),lines=String(r.imeis||'').split(/\n/).map(function(x){return x.trim()}).filter(Boolean);
        for(var k=0;k<n;k++){var parts=(lines[k]||'').split(/[\s,]+/).filter(Boolean),b=blank(r);b.cost=r.cost_mode==='total'&&r.cost?String((+r.cost/n).toFixed(2)):r.cost;b.imei1=parts[0]||'';b.imei2=parts[1]||'';rows.push(b)}});
    }
  }catch(err){}
  if(!rows.length)rows=[blank()];
  if(totalBox)totalBox.hidden=!bulkMode();
  render();
})();

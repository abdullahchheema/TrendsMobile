/* Buy accessories: one line per item, live total, payment that always agrees with the total. The server re-checks everything. */
(function(){
  'use strict';
  var form=document.querySelector('[data-accessories]');if(!form)return;
  var known=JSON.parse(document.getElementById('acc-known').textContent),cats=JSON.parse(document.getElementById('acc-categories').textContent);
  var body=form.querySelector('[data-acc-rows]'),hidden=form.elements.rows_json,status=form.querySelector('[data-status]'),totalEl=form.querySelector('[data-total]'),countEl=form.querySelector('[data-count]');
  var payStatus=form.elements.payment_status,payAmount=form.elements.amount_paid,payHint=form.querySelector('[data-pay-hint]'),payProblem='',rows=[];
  function esc(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}
  function money(v){return 'PKR '+v.toLocaleString('en-PK',{minimumFractionDigits:2,maximumFractionDigits:2})}
  function guessCategory(name){
    var n=String(name).toLowerCase(),map=[['cover','Cover / Case'],['case','Cover / Case'],['protector','Screen Protector'],['glass','Screen Protector'],['charger','Charger'],['adapter','Adapter'],['adaptor','Adapter'],['cable','Cable'],['handsfree','Handsfree / Headphones'],['headphone','Handsfree / Headphones'],['airpod','AirPods / Earbuds'],['earbud','AirPods / Earbuds'],['power bank','Power Bank'],['powerbank','Power Bank'],['watch','Smart Watch']];
    for(var i=0;i<map.length;i++)if(n.indexOf(map[i][0])>-1)return map[i][1];return '';
  }
  function blank(){return {name:'',category:'Other',quantity:'1',cost:'',price:''}}
  function render(focusLast){
    body.innerHTML='';
    rows.forEach(function(r,i){
      var tr=document.createElement('tr');tr.dataset.i=i;
      tr.innerHTML='<td><input data-f="name" list="acc-names" maxlength="180" autocomplete="off" placeholder="e.g. Silicone cover iPhone 15" value="'+esc(r.name)+'"></td>'+
        '<td><select data-f="category">'+cats.map(function(c){return '<option'+(c===r.category?' selected':'')+'>'+esc(c)+'</option>'}).join('')+'</select></td>'+
        '<td><input data-f="quantity" type="number" min="1" step="1" inputmode="numeric" value="'+esc(r.quantity)+'" style="width:80px"></td>'+
        '<td><input data-f="cost" type="number" min="0" step="0.01" inputmode="decimal" data-words value="'+esc(r.cost)+'" placeholder="0.00"></td>'+
        '<td><input data-f="price" type="number" min="0" step="0.01" inputmode="decimal" data-words value="'+esc(r.price)+'" placeholder="0.00"></td>'+
        '<td><button type="button" data-del aria-label="Remove item '+(i+1)+'">&#10005;</button></td>';
      body.appendChild(tr);
    });
    summarise();
    if(window.covison&&window.covison.showWords)body.querySelectorAll('input[data-words]').forEach(window.covison.showWords);
    if(focusLast){var f=body.querySelector('tr:last-child [data-f="name"]');if(f)f.focus()}
  }
  function total(){return rows.reduce(function(s,r){return s+(+r.cost||0)*(parseInt(r.quantity,10)||0)},0)}
  function syncPay(t,typing){
    if(!payStatus||!payAmount)return;
    if(typing&&payStatus.value!=='part'&&payAmount.value!=='')payStatus.value='part';
    var mode=payStatus.value,box=payAmount.closest('.field');
    if(mode==='full')payAmount.value=t?t.toFixed(2):'';if(mode==='unpaid')payAmount.value='';
    if(box)box.hidden=mode!=='part';
    var paid=mode==='full'?t:mode==='part'?(+payAmount.value||0):0;payProblem='';
    if(mode==='part'){
      if(payAmount.value!==''&&paid<0)payProblem='The amount paid cannot be negative.';
      else if(paid>t)payProblem='You cannot pay '+money(paid)+' against a total of only '+money(t)+'. The most you can pay is '+money(t)+'.';
      else if(t>0&&(paid<=0||paid===t))payProblem=paid===t&&paid>0?'That is the whole total. Choose “Paid in full” instead.':'Enter how much is paid now (more than zero, less than the total).';
    }
    if(payHint){
      payHint.textContent=payProblem||('Total '+money(t)+' · Paid now '+money(paid)+' · Still owed to supplier '+money(Math.max(0,t-paid)));
      payHint.className=payProblem?'error':'muted';
      if(!payProblem&&t>0&&window.covison&&window.covison.words)payHint.textContent+=' — '+window.covison.words(t.toFixed(2));
      var bal=(function(){try{return JSON.parse(document.getElementById('pay-balances').textContent)}catch(e){return {}}})(),mth=form.elements.method&&form.elements.method.value;
      if(!payProblem&&paid>0&&mth&&bal[mth]!==undefined&&paid>+bal[mth])payHint.textContent+=' ⚠ The books show only '+money(+bal[mth])+' in '+mth+', so this payment makes it negative. Record the opening cash first (Money In → Money put in by the owner).';
    }
  }
  function summarise(){
    var t=total();totalEl.textContent=money(t);countEl.textContent=rows.length+' item'+(rows.length===1?'':'s');syncPay(t,false);
    hidden.value=JSON.stringify(rows.map(function(r){return {name:r.name,category:r.category,quantity:r.quantity,cost:r.cost,price:r.price}}));
  }
  body.addEventListener('input',function(e){
    var tr=e.target.closest('tr'),f=e.target.dataset.f;if(!tr||!f)return;rows[+tr.dataset.i][f]=e.target.value;
    if(f==='name'&&rows[+tr.dataset.i].category==='Other'){var guess=guessCategory(e.target.value);if(guess){rows[+tr.dataset.i].category=guess;tr.querySelector('[data-f="category"]').value=guess}}
    if(f==='name'){var k=known.filter(function(x){return x.name.toLowerCase()===e.target.value.trim().toLowerCase()})[0];
      if(k){var r=rows[+tr.dataset.i];r.category=k.category;if(!r.price){r.price=k.price;var p=tr.querySelector('[data-f="price"]');p.value=k.price;if(window.covison&&window.covison.showWords)window.covison.showWords(p)}var c=tr.querySelector('[data-f="category"]');if(cats.indexOf(k.category)>-1)c.value=k.category}}
    summarise();
  });
  body.addEventListener('click',function(e){var tr=e.target.closest('tr');if(tr&&e.target.closest('[data-del]')){rows.splice(+tr.dataset.i,1);if(!rows.length)rows.push(blank());render()}});
  body.addEventListener('keydown',function(e){if(e.key==='Enter'&&e.target.tagName==='INPUT'){e.preventDefault();if(e.target.dataset.f==='price'&&+tr0(e.target)===rows.length-1){rows.push(blank());render(true)}}});
  function tr0(el){return el.closest('tr').dataset.i}
  form.querySelector('[data-acc-add]').addEventListener('click',function(){rows.push(blank());render(true)});
  if(payStatus)payStatus.addEventListener('change',summarise);
  if(form.elements.method)form.elements.method.addEventListener('change',summarise);
  if(payAmount)payAmount.addEventListener('input',function(){syncPay(total(),true);summarise()});
  form.addEventListener('submit',function(e){
    summarise();
    function stop(msg){e.preventDefault();status.textContent=msg;status.className='error'}
    for(var i=0;i<rows.length;i++){var r=rows[i];
      if(!String(r.name).trim()){stop('Item '+(i+1)+': enter the name.');return}
      if(!(parseInt(r.quantity,10)>0)){stop('Item '+(i+1)+': enter how many.');return}
      if(!(+r.cost>0)){stop('Item '+(i+1)+': enter what each costs you.');return}
      if(!(+r.price>0)){stop('Item '+(i+1)+': enter the selling price.');return}
    }
    if(payProblem){stop(payProblem);if(payAmount)payAmount.focus()}
  },true);
  try{var saved=JSON.parse(hidden.value||'[]');if(Array.isArray(saved))saved.forEach(function(r){rows.push({name:r.name||'',category:r.category||'Other',quantity:String(r.quantity||'1'),cost:r.cost||'',price:r.price||''})})}catch(err){}
  if(!rows.length)rows=[blank()];
  render();
})();

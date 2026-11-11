/* Shows an amount in words under money boxes: 2527000 -> "Twenty Five Lakh Twenty Seven Thousand Rupees Only".
   Any <input data-words> gets a line under it; covison.words(n) can be used by other scripts. Same rules as shop/words.py. */
(function(){
  'use strict';
  var ONES=['','One','Two','Three','Four','Five','Six','Seven','Eight','Nine','Ten','Eleven','Twelve','Thirteen','Fourteen','Fifteen','Sixteen','Seventeen','Eighteen','Nineteen'];
  var TENS=['','','Twenty','Thirty','Forty','Fifty','Sixty','Seventy','Eighty','Ninety'];
  var SCALES=[[1e9,'Arab'],[1e7,'Crore'],[1e5,'Lakh'],[1000,'Thousand'],[100,'Hundred']];
  function below100(n){return n<20?ONES[n]:TENS[Math.floor(n/10)]+(n%10?' '+ONES[n%10]:'')}
  function whole(n){
    if(n===0)return 'Zero';var parts=[];
    SCALES.forEach(function(s){if(n>=s[0]){var c=Math.floor(n/s[0]);n-=c*s[0];parts.push(whole(c)+' '+s[1])}});
    if(n)parts.push(below100(n));return parts.join(' ');
  }
  function words(value){
    var raw=String(value==null?'':value).replace(/,/g,'').trim();if(raw===''||isNaN(Number(raw)))return '';
    var amount=Number(raw);if(!isFinite(amount)||Math.abs(amount)>9.99e14)return '';
    var cents=Math.round(Math.abs(amount)*100),rupees=Math.floor(cents/100),paisa=cents%100;
    return (amount<0?'Minus ':'')+whole(rupees)+' Rupees'+(paisa?' and '+whole(paisa)+' Paisa':'')+' Only';
  }
  window.covison=window.covison||{};window.covison.words=words;
  function line(input){
    var next=input.nextElementSibling;
    if(next&&next.classList&&next.classList.contains('words-line'))return next;
    var el=document.createElement('small');el.className='words-line muted';el.setAttribute('aria-live','polite');input.insertAdjacentElement('afterend',el);return el;
  }
  function show(input){var v=input.value,text=Number(v)>0?words(v):'';var el=line(input);el.textContent=text;el.hidden=!text}
  var ready=(window.covison.page&&window.covison.page.ready)||function(f){f()};
  document.addEventListener('input',function(e){if(e.target.matches&&e.target.matches('input[data-words],input[name=amount],input[name=amount_paid],input[name=price],input[name=bulk_total],input[name=opening_float],input[name=payment_amount]'))show(e.target)});
  ready(function(){document.querySelectorAll('input[data-words],input[name=amount],input[name=amount_paid],input[name=price],input[name=bulk_total],input[name=opening_float],input[name=payment_amount]').forEach(show)});
  window.covison.showWords=show;
})();

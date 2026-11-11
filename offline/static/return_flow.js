/* Returns: live value of what is ticked, and show the refund box only when refunding. The server decides everything. */
(function(){
  'use strict';
  var form=document.querySelector('[data-return-flow]');if(!form)return;
  var total=form.querySelector('[data-value-total]'),box=form.querySelector('[data-refund-box]');
  function money(v){return 'PKR '+v.toLocaleString('en-PK',{minimumFractionDigits:2,maximumFractionDigits:2})}
  function refresh(){
    var sum=0;
    form.querySelectorAll('input[type=checkbox][name=devices]:checked').forEach(function(c){sum+=+c.dataset.value||0});
    form.querySelectorAll('input[data-value-each]').forEach(function(i){sum+=(+i.dataset.valueEach||0)*(+i.value||0)});
    total.textContent=money(sum);
    var r=form.querySelector('input[name=resolution]:checked');if(box)box.hidden=!(r&&r.value==='refund');
  }
  form.addEventListener('input',refresh);form.addEventListener('change',refresh);refresh();
})();

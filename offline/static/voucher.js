/* Voucher form: show only the account group that fits the chosen voucher type (the server still validates). */
(function(){
  var form=document.querySelector('[data-voucher]');if(!form)return;
  var type=form.elements.type,account=form.elements.account,ref=form.elements.reference;
  var group={expense:'Expense accounts',capital:'Owner equity accounts',drawings:'Owner equity accounts',loan_in:'Loan and liability accounts',loan_out:'Loan and liability accounts'};
  function sync(){
    var want=group[type.value];
    Array.prototype.forEach.call(account.querySelectorAll('optgroup'),function(g){var on=g.label===want;g.hidden=!on;g.disabled=!on});
    var sel=account.options[account.selectedIndex];if(sel&&sel.parentNode.tagName==='OPTGROUP'&&sel.parentNode.disabled)account.value='';
  }
  type.addEventListener('change',sync);sync();
})();

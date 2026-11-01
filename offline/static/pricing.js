/* Integer-paisa preview using the server's half-even rounding. Posting is authoritative. */
(()=>{
 'use strict';
 const scaled=(value,places)=>{
  const text=String(value??'0').trim(),match=/^(\d*)(?:\.(\d+))?$/.exec(text),fraction=(match?.[2]||'').replace(/0+$/,'');
  if(!match||(!match[1]&&!match[2])||fraction.length>places)throw new Error('Enter nonnegative amounts with at most two decimals and quantities with at most three.');
  return BigInt((match[1]||'0')+fraction.padEnd(places,'0'));
 };
 const halfEven=(numerator,denominator)=>{const whole=numerator/denominator,rest=numerator%denominator;return whole+(rest*2n>denominator||(rest*2n===denominator&&whole%2n)?1n:0n);};
 const decimal=value=>`${value/100n}.${String(value%100n).padStart(2,'0')}`;
 function calculate(items,spec={},rule=null){
  const subtotal=items.reduce((sum,row)=>sum+halfEven(scaled(row.quantity,3)*scaled(row.rate,2),1000n),0n);
  const value=scaled(spec.discount_value||'0',2),kind=spec.discount_type||'amount';
  if(!['amount','percent'].includes(kind)||(kind==='percent'&&value>10000n))throw new Error('Discount percentage must be between 0 and 100.');
  const discount=kind==='percent'?halfEven(subtotal*value,10000n):value;
  if(discount>subtotal)throw new Error('Invoice discount exceeds the item subtotal.');
  const net=subtotal-discount,tax=rule?halfEven(net*scaled(rule.rate,4),1000000n):0n;
  return {subtotal:decimal(subtotal),discount:decimal(discount),net:decimal(net),tax:decimal(tax),total:decimal(net+tax)};
 }
 window.covisonPricing={calculate};
})();

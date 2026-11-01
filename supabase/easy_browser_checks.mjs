import fs from 'node:fs';
import path from 'node:path';

// Real submissions are allowed only when the runner explicitly uses a database clone.
export async function checkEasyRetail({cdp,evaluate,navigate,base,output,isolated}) {
 const result={isolated_database_submissions:0};
 const capture=async name=>{const dimensions=await evaluate('({width:innerWidth,height:Math.min(document.documentElement.scrollHeight,10000)})');const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,...dimensions,scale:1}});fs.writeFileSync(path.join(output,name),Buffer.from(shot.data,'base64'));};
 await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
 await navigate(base+'/sales-accounts/');
 result.liveWorkspace=await evaluate("document.querySelector('h1').textContent==='Sales & Accounts'&&!document.body.textContent.includes('Demo shop practice')");
 await capture('sales-and-accounts.png');
 const sampleShop=await evaluate("Array.from(document.querySelector('[name=branch]').options).find(option=>option.textContent.includes('DEMO Mobile Shop'))?.value");
 if(sampleShop)await navigate(base+'/sales-accounts/?branch='+encodeURIComponent(sampleShop));
 result.demoReconciles=await evaluate("document.body.textContent.includes('40,000.00')&&document.body.textContent.includes('110,000.00')&&document.body.textContent.includes('380,000.00')");
 const purchaseUrl=await evaluate("document.querySelector('.app-main a[href*=\"/entry/purchase/\"]').getAttribute('href')");
 const saleUrl=await evaluate("document.querySelector('.app-main a[href*=\"/entry/sale/\"]').getAttribute('href')");
 await capture('demo-shop-accounts.png');
 await navigate(base+purchaseUrl);
 result.purchaseControls=await evaluate("(()=>{const f=document.querySelector('[data-easy-entry]'),e=f.elements;e.party_name.value='QA Counter Supplier';e.product.value=Array.from(e.product.options).find(o=>o.value)?.value||'';e.quantity.value='2';e.pta_status.value='Non-PTA';e.price.value='60000';e.asking_price.value='75000';e.price.dispatchEvent(new Event('input'));const total=f.querySelector('[data-easy-total]').textContent.includes('120,000.00');e.payment_status.value='part';e.amount_paid.value='25000';e.payment_status.dispatchEvent(new Event('input'));const partial=!e.amount_paid.closest('.field').hidden&&f.querySelector('[data-easy-balance]').textContent.includes('95,000.00');e.payment_status.value='unpaid';e.payment_status.dispatchEvent(new Event('input'));return total&&partial&&e.amount_paid.closest('.field').hidden&&f.checkValidity()&&!/IMEI/i.test(document.body.innerText);})()");
 await capture('easy-purchase-desktop.png');
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
 result.mobilePurchase=await evaluate('document.documentElement.scrollWidth<=innerWidth+2');
 await capture('easy-purchase-mobile.png');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
 if(isolated){
  await evaluate("(()=>{const f=document.querySelector('[data-easy-entry]');f.elements.quantity.value='1';window.qaPurchaseBody=Array.from(new FormData(f).entries());f.requestSubmit();})()");
  for(let i=0;i<100;i++){if(await evaluate("location.pathname.startsWith('/entry/done/')"))break;await new Promise(r=>setTimeout(r,100));}
  result.purchaseSaved=await evaluate("location.pathname.startsWith('/entry/done/')&&document.body.textContent.includes('Purchase saved')&&document.body.textContent.includes('60,000.00')");
  if(!result.purchaseSaved)throw Error('Isolated quick purchase did not save.');
  result.isolated_database_submissions++;
  const stock=await evaluate("document.querySelector('tbody td small').textContent.replace('Stock ref ','').trim()");
  await capture('easy-purchase-confirmation.png');
  await navigate(base+saleUrl);
  const prepared=await evaluate("(()=>{const f=document.querySelector('[data-easy-entry]'),e=f.elements;e.party_name.value='QA Counter Customer';const choice=Array.from(e.phone.options).find(o=>o.textContent.includes("+JSON.stringify(stock)+"));if(!choice)return false;e.phone.value=choice.value;e.phone.dispatchEvent(new Event('change'));e.payment_status.value='full';e.payment_status.dispatchEvent(new Event('input'));return Number(e.price.value)===75000&&e.pta_status.value==='Non-PTA'&&f.checkValidity();})()");
  result.saleAutofill=prepared;
  await capture('easy-sale-desktop.png');
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
  result.mobileSale=await evaluate('document.documentElement.scrollWidth<=innerWidth+2');
  await capture('easy-sale-mobile.png');
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate("document.querySelector('[data-easy-entry]').requestSubmit()");
  for(let i=0;i<100;i++){if(await evaluate("location.pathname.startsWith('/entry/done/')"))break;await new Promise(r=>setTimeout(r,100));}
  result.saleSaved=await evaluate("location.pathname.startsWith('/entry/done/')&&document.body.textContent.includes('Sale saved')&&document.body.textContent.includes('75,000.00')&&document.body.textContent.includes('0.00')");
  result.isolated_database_submissions++;
  await capture('easy-sale-confirmation.png');
  const receipt=await evaluate("document.querySelector('a[href^=\"/receipt/\"]')?.getAttribute('href')");
  if(receipt){await navigate(base+receipt);result.receiptPtaOnly=await evaluate("document.body.textContent.includes('Non-PTA')&&!/IMEI/i.test(document.body.innerText)");}
 }
 result.passed=result.liveWorkspace&&result.demoReconciles&&result.purchaseControls&&result.mobilePurchase&&(!isolated||(result.purchaseSaved&&result.saleSaved&&result.saleAutofill&&result.mobileSale&&result.receiptPtaOnly));
 fs.writeFileSync(path.join(output,'easy-retail-checks.json'),JSON.stringify(result,null,2));
 return result;
}

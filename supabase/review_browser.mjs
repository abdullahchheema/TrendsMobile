// Isolated headless browser QA. Never reads an existing browser profile.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
const root=path.dirname(new URL(import.meta.url).pathname);
const env=Object.fromEntries(fs.readFileSync(path.join(root,'server/.env'),'utf8').split('\n').filter(l=>l&&!l.startsWith('#')&&l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
const phase=process.argv[2]||'after';if(!['before','after'].includes(phase))throw Error('Use before or after.');
const output=process.argv.includes('--device-tests')?path.join(root,'artifacts/device-review'):path.join(root,'artifacts/screen-review',phase);fs.mkdirSync(output,{recursive:true});
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'covison-browser-review-'));
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--no-first-run','--no-default-browser-check','--disable-sync','--disable-background-networking','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore',fs.openSync(path.join(output,'browser.log'),'w')]});
const pause=ms=>new Promise(r=>setTimeout(r,ms));let socket;let next=0;const pending=new Map();const exceptions=[];let signedIn=false;let result={};let requestInterceptor=null;
try{
 let port;
 for(let i=0;i<150;i++){
  try{port=fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0];break;}catch{await pause(100);}
 }
 if(!port)throw Error('Isolated Chrome did not start.');
 const targets=await (await fetch('http://127.0.0.1:'+port+'/json/list')).json();
 const target=targets.find(t=>t.type==='page');socket=new WebSocket(target.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 socket.addEventListener('message',event=>{
  const message=JSON.parse(event.data);
  if(message.id&&pending.has(message.id)){const waiter=pending.get(message.id);pending.delete(message.id);clearTimeout(waiter.timeout);message.error?waiter.reject(Error(message.error.message)):waiter.resolve(message.result);}
  if(message.method==='Runtime.exceptionThrown')exceptions.push(message.params.exceptionDetails.text);
  if(message.method==='Fetch.requestPaused'&&requestInterceptor)requestInterceptor(message.params).catch(error=>{exceptions.push('QA interception failed');});
 });
 const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;const timeout=setTimeout(()=>{pending.delete(id);reject(Error('Browser command timed out: '+method));},20000);pending.set(id,{resolve,reject,timeout});socket.send(JSON.stringify({id,method,params}));});
 const evaluate=async expression=>{const r=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('Page evaluation failed.');return r.result.value;};
 await cdp('Page.enable');await cdp('Runtime.enable');
 const navigate=async url=>{
  await cdp('Page.navigate',{url});
  for(let i=0;i<150;i++){if(await evaluate('document.readyState')==='complete'&&await evaluate('location.href')===url)return;await pause(80);}
  if(await evaluate('document.readyState')!=='complete')throw Error('Page did not load.');
 };
 const base=process.argv.find(v=>v.startsWith('--base-url='))?.slice(11)||env.COVISON_UI_URL;
 await navigate(base+'/login/');
 await evaluate(`(()=>{document.querySelector('[name=username]').value=${JSON.stringify(env.COVISON_CRM_OWNER_EMAIL)};document.querySelector('[name=password]').value=${JSON.stringify(env.COVISON_CRM_OWNER_PASSWORD)};document.querySelector('form').requestSubmit();return true;})()`);
 for(let i=0;i<150;i++){if((await evaluate('location.pathname'))==='/desk/mobile-shop'){signedIn=true;break;}await pause(100);}
 if(!signedIn)throw Error('Owner login failed; no credentials were logged.');
 await pause(250);
 const nav=await evaluate(`Array.from(document.querySelectorAll('.sidebar a')).map(a=>a.getAttribute('href')).filter(p=>p&&p.startsWith('/'))`);
 const device=await evaluate(`document.querySelector('a[href^="/device/"]')?.getAttribute('href')`);
 const paths=[...new Set(['/desk/mobile-shop','/desk/point-of-sale',...nav,...['Mobile Intake','Purchase Receipt','Purchase Invoice','Sales Invoice','Payment Entry','Stock Entry','Mobile Exchange','Warranty Claim','Mobile Daily Closing','POS Opening Entry','POS Closing Entry'].map(k=>'/documents/'+encodeURIComponent(k)+'/new/'),'/master/Till/new/','/master/Mobile%20Staff/new/','/master/Item/new/',device].filter(Boolean))];
 if(nav.includes('/setup/')){
  await navigate(base+'/setup/');
  const finance=await evaluate(`document.querySelector('a[href$="/finance/"]')?.getAttribute('href')`);
  paths.push('/setup/new/','/master/Account/new/','/setup/tax/','/setup/tax/new/');if(finance)paths.push(finance);
 }
 if(nav.includes('/device-check/'))paths.push('/device-check/print/?paper=58','/device-check/print/?paper=80','/device-check/print/?paper=a4');
 if(nav.includes('/fund-transfers/'))paths.push('/fund-transfers/new/');
 if(nav.includes('/journals/'))paths.push('/journals/new/');
 let receipt;let phoneReceipt;
 await navigate(base+'/records/Sales%20Invoice/');
 const firstSale=await evaluate(`Array.from(document.querySelectorAll('tbody tr')).filter(r=>r.innerText.includes('Submitted')).map(r=>r.querySelector('a')?.getAttribute('href')).find(Boolean)`);
 const saleLinks=await evaluate(`Array.from(document.querySelectorAll('tbody tr')).filter(r=>r.innerText.includes('Submitted')).map(r=>r.querySelector('a')?.getAttribute('href')).filter(Boolean).slice(0,50)`);
 if(firstSale){await navigate(base+firstSale);receipt=await evaluate(`document.querySelector('a[href^="/receipt/"]')?.getAttribute('href')`);paths.push(firstSale);if(receipt)paths.push(receipt,receipt+'?paper=a4',receipt+'?paper=58');}
 for(const link of saleLinks){await navigate(base+link);const hasImei=await evaluate(`Array.from(document.querySelectorAll('td small')).some(e=>/[0-9]{15}/.test(e.textContent))`);if(hasImei){phoneReceipt=await evaluate(`document.querySelector('a[href^="/receipt/"]')?.getAttribute('href')`);if(phoneReceipt&&phoneReceipt!==receipt)paths.push(phoneReceipt);break;}}
 const screens=[];
 for(const [index,route] of (process.argv.includes('--interactions-only')?[]:paths).entries()){
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
  await navigate(base+route);await pause(100);
  const meta=await evaluate(`(()=>({path:location.pathname,title:document.title,heading:document.querySelector('h1')?.textContent,fields:Array.from(document.querySelectorAll('input,select,textarea')).map(e=>({name:e.name,disabled:e.disabled,type:e.type})).filter(e=>!['csrfmiddlewaretoken','password','password1','password2'].includes(e.name)),pageOverflow:document.documentElement.scrollWidth>innerWidth+2,viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,bodyHeight:document.documentElement.scrollHeight,errorPage:/Server Error|Traceback|Forbidden|Not Found/.test(document.querySelector('h1')?.textContent||'')}))()`);
  const filename=String(index+1).padStart(2,'0')+'-'+route.split('?')[0].replace(/[^a-z0-9]+/gi,'-').replace(/^-|-$/g,'').slice(0,100);
  const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1365,height:Math.min(meta.bodyHeight,10000),scale:1}});
  fs.writeFileSync(path.join(output,filename+'.png'),Buffer.from(shot.data,'base64'));meta.screenshot=filename+'.png';screens.push(meta);
  if(['/desk/mobile-shop','/desk/point-of-sale','/documents/Mobile%20Intake/new/','/master/Item/new/'].includes(route)||route.startsWith('/desk/query-report/Mobile%20Inventory')||route===receipt||route===phoneReceipt){
   await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});await pause(100);
   const mobile=await evaluate(`({pageOverflow:document.documentElement.scrollWidth>innerWidth+2,viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,bodyHeight:document.documentElement.scrollHeight})`);
   const image=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:390,height:Math.min(mobile.bodyHeight,10000),scale:1}});
   fs.writeFileSync(path.join(output,filename+'-mobile.png'),Buffer.from(image.data,'base64'));meta.mobile={...mobile,screenshot:filename+'-mobile.png'};
  }
  fs.writeFileSync(path.join(output,'progress.json'),JSON.stringify({screens,exceptions},null,2));
  if((index+1)%10===0)console.log('Reviewed '+(index+1)+' / '+paths.length+' screens');
 }
 // Inspect an existing accessory return form without submitting it.
 await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:900,deviceScaleFactor:1,mobile:false});
 let returnFormChecks={found:false};
 for(const link of saleLinks){
  await navigate(base+link);
  const candidate=await evaluate(`(()=>{const form=document.querySelector('[data-return-form]');const qty=form?.querySelector('input[name^=return_qty_]:not(:disabled)');return Boolean(qty);})()`);
  if(!candidate)continue;
  returnFormChecks=await evaluate(`(()=>{const form=document.querySelector('[data-return-form]'),qty=form.querySelector('input[name^=return_qty_]:not(:disabled)');form.closest('details').open=true;const max=Number(qty.max);qty.value=String(max+0.001);const excessBlocked=!qty.checkValidity();qty.value=String(Math.min(max,0.5));return {found:true,maximum:max,partialQuantityValid:qty.checkValidity(),excessQuantityBlocked:excessBlocked,priceNotEditable:!form.querySelector('[name=rate]'),warehousePresent:form.querySelector('[name=return_warehouse]').options.length>0,selectionPostedByOriginalLine:new FormData(form).has(qty.name)};})()`);
  const returnHeight=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);
  const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1365,height:returnHeight,scale:1}});fs.writeFileSync(path.join(output,'accessory-return.png'),Buffer.from(shot.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});await pause(100);
  returnFormChecks.mobileNoPageOverflow=await evaluate(`document.documentElement.scrollWidth<=innerWidth+2`);
  const height=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);
  const mobile=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:390,height,scale:1}});fs.writeFileSync(path.join(output,'accessory-return-mobile.png'),Buffer.from(mobile.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:900,deviceScaleFactor:1,mobile:false});break;
 }
 returnFormChecks.passed=returnFormChecks.found&&returnFormChecks.partialQuantityValid&&returnFormChecks.excessQuantityBlocked&&returnFormChecks.priceNotEditable&&returnFormChecks.warehousePresent&&returnFormChecks.selectionPostedByOriginalLine&&returnFormChecks.mobileNoPageOverflow;
 fs.writeFileSync(path.join(output,'return-form-checks.json'),JSON.stringify(returnFormChecks,null,2));
 if(phase==='after'&&!returnFormChecks.passed)process.exitCode=1;
 // Inspect transfer entry only; never submit a real movement from this browser.
 let fundFormChecks={available:false};
 if(nav.includes('/fund-transfers/')){
  await navigate(base+'/fund-transfers/new/');
  fundFormChecks=await evaluate(`(()=>{const form=document.querySelector('form[method=post].panel');if(!form)return {available:false};form.querySelector('[name=source_method]').value='Cash';form.querySelector('[name=target_method]').value='Bank Transfer';const amount=form.querySelector('[name=amount]');amount.value='500.001';const extraPrecisionBlocked=!amount.checkValidity();amount.value='500.25';const handler=form.querySelector('[name=handler]');handler.value=Array.from(handler.options).find(o=>o.value)?.value||'';form.querySelector('[name=cash_scope]').value='shop';form.querySelector('[name=outside_till_confirmed]').checked=true;form.querySelector('[name=transfer_reference]').value='BROWSER-CHECK-NOT-POSTED';form.querySelector('[name=notes]').value='Unsaved browser interaction check';return {available:true,extraPrecisionBlocked,validCompletedForm:form.checkValidity(),requestKeyPresent:/^[0-9a-f-]{36}$/.test(form.querySelector('[name=request_key]').value),cashCustodyChoices:form.querySelector('[name=cash_scope]').options.length===3,noPartySelector:!form.querySelector('[name=customer],[name=supplier]'),businessSubmitted:false};})()`);
  const height=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);
  const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1365,height,scale:1}});fs.writeFileSync(path.join(output,'fund-transfer-form.png'),Buffer.from(shot.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});await pause(100);
  fundFormChecks.mobileNoPageOverflow=await evaluate(`document.documentElement.scrollWidth<=innerWidth+2`);
  const mobileHeight=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);
  const mobile=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:390,height:mobileHeight,scale:1}});fs.writeFileSync(path.join(output,'fund-transfer-form-mobile.png'),Buffer.from(mobile.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:900,deviceScaleFactor:1,mobile:false});
 }
 fundFormChecks.passed=fundFormChecks.available&&fundFormChecks.extraPrecisionBlocked&&fundFormChecks.validCompletedForm&&fundFormChecks.requestKeyPresent&&fundFormChecks.cashCustodyChoices&&fundFormChecks.noPartySelector&&fundFormChecks.mobileNoPageOverflow;
 fs.writeFileSync(path.join(output,'fund-transfer-form-checks.json'),JSON.stringify(fundFormChecks,null,2));
 if(phase==='after'&&!fundFormChecks.passed)process.exitCode=1;
 // Fill an unsaved owner journal and exercise row creation; never submit it.
 let journalFormChecks={available:false};
 if(nav.includes('/journals/')){
  await navigate(base+'/journals/new/');
  journalFormChecks=await evaluate(`(()=>{const form=document.getElementById('journal-form');if(!form)return {available:false};const accounts=Array.from(form.querySelector('[name="rows-0-account"]').options).filter(o=>o.value);if(accounts.length<2)return {available:true,enoughAccounts:false};form.querySelector('[name=supporting_reference]').value='BROWSER-CHECK-NOT-POSTED';form.querySelector('[name=notes]').value='Unsaved owner journal interaction check';for(const i of [0,1])form.querySelector('[name="rows-'+i+'-account"]').value=accounts[i].value;const debit=form.querySelector('[name="rows-0-debit"]');debit.value='125.001';const extraPrecisionBlocked=!debit.checkValidity();debit.value='125.25';form.querySelector('[name="rows-1-credit"]').value='125.25';const validCompletedForm=form.checkValidity();form.querySelector('#add-journal-row').click();const thirdRowPresent=form.querySelector('[name="rows-2-account"]')!==null&&form.querySelector('[name="rows-TOTAL_FORMS"]').value==='3';form.querySelector('[name="rows-2-account"]').value=accounts[0].value;form.querySelector('[name="rows-2-debit"]').value='1';const rowCanBeMarkedForRemoval=form.querySelector('[name="rows-2-DELETE"]')!==null;form.querySelector('[name="rows-2-DELETE"]').checked=true;return {available:true,enoughAccounts:true,extraPrecisionBlocked,validCompletedForm,thirdRowPresent,rowCanBeMarkedForRemoval,requestKeyPresent:/^[0-9a-f-]{36}$/.test(form.querySelector('[name=request_key]').value),businessSubmitted:false};})()`);
  const height=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1365,height,scale:1}});fs.writeFileSync(path.join(output,'journal-form.png'),Buffer.from(shot.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
  journalFormChecks.mobileNoPageOverflow=await evaluate(`document.documentElement.scrollWidth<=innerWidth+2`);
  const mobileHeight=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);const mobile=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:390,height:mobileHeight,scale:1}});fs.writeFileSync(path.join(output,'journal-form-mobile.png'),Buffer.from(mobile.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
 }
 journalFormChecks.passed=journalFormChecks.available&&journalFormChecks.enoughAccounts&&journalFormChecks.extraPrecisionBlocked&&journalFormChecks.validCompletedForm&&journalFormChecks.thirdRowPresent&&journalFormChecks.rowCanBeMarkedForRemoval&&journalFormChecks.requestKeyPresent&&journalFormChecks.mobileNoPageOverflow;
 fs.writeFileSync(path.join(output,'journal-form-checks.json'),JSON.stringify(journalFormChecks,null,2));if(phase==='after'&&!journalFormChecks.passed)process.exitCode=1;
 // A real browser checks that the report refresh keeps selected controls.
 await navigate(base+'/desk/query-report/Mobile%20Inventory');
 const filterInteraction=await evaluate(`(()=>{const s=document.querySelector('select[name=status]');if(!s)return false;s.value='Sold';s.form.requestSubmit();return true;})()`);
 await pause(500);
 const filterRetained=filterInteraction&&await evaluate(`document.querySelector('select[name=status]')?.value==='Sold' && new URL(location.href).searchParams.get('status')==='Sold'`);
 let posCartInteraction=false;let posCartDetails={};let mobileMenuInteraction=false;
 if(phase==='after'){
  await navigate(base+'/desk/point-of-sale');await pause(150);
  posCartDetails=await evaluate(`(()=>{const items=JSON.parse(document.getElementById('pos-data').textContent);const phone=items.find(p=>p.serialized);if(!phone)return {passed:false,stage:'no phone'};const card=Array.from(document.querySelectorAll('#pos-products button')).find(b=>b.textContent.includes(phone.code));if(!card)return {passed:false,stage:'no product button',code:phone.code};card.click();card.click();const rows=document.querySelectorAll('.cart-row');if(rows.length!==1||rows[0].querySelector('input').value!=='1')return {passed:false,stage:'serialized duplicate',rows:rows.length,quantity:rows[0]?.querySelector('input')?.value};const rate=rows[0].querySelectorAll('input')[1];rate.value='12345.50';rate.dispatchEvent(new Event('change'));const total=document.getElementById('cart-total').textContent;if(!total.includes('12,345.50'))return {passed:false,stage:'rate update',total};const form=document.getElementById('pos-cart');form.elements.discount_value.value='123.45';form.elements.discount_value.dispatchEvent(new Event('change'));const amountDiscount=document.getElementById('cart-total').textContent.includes('12,222.05');form.elements.discount_type.value='percent';form.elements.discount_value.value='12.50';form.elements.discount_value.dispatchEvent(new Event('change'));const percentDiscount=document.getElementById('cart-total').textContent.includes('10,802.31');const calculate=window.covisonPricing.calculate;const halfEvenTax=calculate([{quantity:'1',rate:'0.10'}],{},{rate:'5.0000'}).tax==='0.00'&&calculate([{quantity:'1',rate:'0.30'}],{},{rate:'5.0000'}).tax==='0.02';const fractionalLine=calculate([{quantity:'.5',rate:'0.01'}]).total==='0.00';const noPresetRules=JSON.parse(document.getElementById('pos-tax-rules').textContent).length===0;form.elements.discount_type.value='amount';form.elements.discount_value.value='0';document.querySelector('.cart-row button').click();return {passed:document.querySelectorAll('.cart-row').length===0&&amountDiscount&&percentDiscount&&halfEvenTax&&fractionalLine&&noPresetRules,stage:'remove',rows:document.querySelectorAll('.cart-row').length,total,amountDiscount,percentDiscount,halfEvenTax,fractionalLine,noPresetRules};})()`);
  posCartInteraction=posCartDetails.passed;
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
  await navigate(base+'/desk/mobile-shop');await pause(100);
  mobileMenuInteraction=await evaluate(`(()=>{const button=document.querySelector('[data-menu-toggle]');if(!button||getComputedStyle(button).display==='none')return false;button.click();const visible=button.getAttribute('aria-expanded')==='true'&&getComputedStyle(document.querySelector('.sidebar')).display!=='none'&&Array.from(document.querySelectorAll('.sidebar details')).every(e=>getComputedStyle(e).display!=='none');button.click();return visible&&button.getAttribute('aria-expanded')==='false';})()`);
 }
 // Exercise reports with read-only requests against the candidate image.
 let reportChecks={};
 if(phase==='after'){
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
  await navigate(base+'/reports/');
  reportChecks.catalog=await evaluate("document.querySelectorAll('.report-cards article').length===17 && document.body.textContent.includes('Stock Ledger') && document.body.textContent.includes('Phone Stock')");
  const captureReport=async filename=>{const dimensions=await evaluate('({width:innerWidth,height:Math.min(document.documentElement.scrollHeight,10000)})');const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,...dimensions,scale:1}});fs.writeFileSync(path.join(output,filename),Buffer.from(shot.data,'base64'));};
  await captureReport('report-center.png');
  await navigate(base+'/reports/?search=phone+stock');
  reportChecks.catalogSearch=await evaluate("document.querySelectorAll('.report-cards article').length===1 && document.querySelector('.report-cards article').textContent.includes('Mobile Inventory')");
  await navigate(base+'/desk/query-report/Sales%20Register');
  const allHistory=await evaluate("Array.from(document.querySelectorAll('.report-presets a')).find(a=>a.textContent==='All history')?.getAttribute('href')");
  if(!allHistory)throw Error('Report date presets missing.');
  await navigate(base+'/desk/query-report/Sales%20Register'+allHistory);
  reportChecks.allHistory=await evaluate("Array.from(document.querySelectorAll('.report-table tbody tr')).some(r=>r.cells.length>1)");
  const sort=await evaluate("Array.from(document.querySelectorAll('.report-sort')).find(a=>a.textContent==='Total')?.getAttribute('href')");
  await navigate(base+'/desk/query-report/Sales%20Register'+sort);
  const ascending=await evaluate("(()=>{const headers=Array.from(document.querySelectorAll('thead th'));const index=headers.findIndex(h=>h.textContent.trim().startsWith('Total'));const values=Array.from(document.querySelectorAll('tbody tr')).map(r=>Number(r.cells[index].textContent.replaceAll(',','')));return {passed:values.every((v,i)=>!i||v>=values[i-1]),invoice:document.querySelector('tbody a')?.textContent};})()");
  reportChecks.numericSort=ascending.passed;
  const sortedUrl=await evaluate('location.href');
  await navigate(sortedUrl+'&search='+encodeURIComponent(ascending.invoice));
  reportChecks.filteredTotals=await evaluate("document.querySelectorAll('tbody tr').length===1 && document.querySelector('tfoot')?.textContent.includes('Filtered total')");
  reportChecks.csv=await evaluate("fetch(location.href+'&format=csv').then(async r=>r.ok && r.headers.get('content-type').includes('text/csv') && (await r.text()).trim().split('\\n').length===2)");
  reportChecks.sourceLinks=await evaluate("Boolean(document.querySelector('tbody a')?.getAttribute('href').startsWith('/'))");
  await captureReport('sales-report-filtered.png');
  await cdp('Emulation.setEmulatedMedia',{media:'print'});
  reportChecks.print=await evaluate("getComputedStyle(document.querySelector('.report-filters')).display==='none' && getComputedStyle(document.querySelector('.report-controls')).display==='none' && getComputedStyle(document.querySelector('thead')).display==='table-header-group' && document.querySelector('.report-period').textContent.includes('2000-01-01')");
  await cdp('Emulation.setEmulatedMedia',{media:''});
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
  reportChecks.mobileNoPageOverflow=await evaluate('document.documentElement.scrollWidth<=innerWidth+2');
  await captureReport('sales-report-filtered-mobile.png');
  await navigate(base+'/reports/');
  reportChecks.mobileCatalogNoPageOverflow=await evaluate('document.documentElement.scrollWidth<=innerWidth+2');
  await captureReport('report-center-mobile.png');
  const financialChecks=[];
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
  for(const name of ['Profit and Loss Statement','Balance Sheet','Trial Balance']){
   await navigate(base+'/desk/query-report/'+encodeURIComponent(name)+'?from_date=2000-01-01&to_date=2099-12-31');
   const check=await evaluate("(()=>{const values=Array.from(document.querySelectorAll('.statement-cards strong')).map(e=>Number(e.textContent.replaceAll(',','')));const last=values.at(-1);return {cards:values.length,values,hasRows:Array.from(document.querySelectorAll('tbody tr')).some(r=>r.cells.length>1),drilldown:document.querySelector('tbody a')?.getAttribute('href'),print:document.querySelector('[data-print]')!==null};})()");
   const reconciles=name==='Profit and Loss Statement'?Math.abs(check.values[0]-check.values[1]-check.values[2])<0.005:check.values.at(-1)===0;
   const drilldownWorks=check.drilldown?await evaluate('fetch('+JSON.stringify(check.drilldown)+').then(r=>r.status===200)'):false;
   const csvWorks=await evaluate("fetch(location.href+'&format=csv').then(r=>r.ok&&r.headers.get('content-type').includes('text/csv'))");
   financialChecks.push({name,reconciles,drilldownWorks,csvWorks,cards:check.cards,values:check.values,hasRows:check.hasRows});
   await captureReport('financial-'+name.toLowerCase().replaceAll(' ','-')+'.png');
  }
  reportChecks.financialStatements=financialChecks.every(c=>c.reconciles&&c.drilldownWorks&&c.csvWorks&&c.hasRows&&c.cards>=3);
  fs.writeFileSync(path.join(output,'financial-checks.json'),JSON.stringify(financialChecks,null,2));
  reportChecks.passed=Object.values(reportChecks).every(v=>v===true);
  fs.writeFileSync(path.join(output,'report-checks.json'),JSON.stringify(reportChecks,null,2));
  if(!reportChecks.passed)process.exitCode=1;
 }
 let customerEntryChecks={};
 if(phase==='after'){
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
  await navigate(base+'/desk/point-of-sale');
  customerEntryChecks.pos=await evaluate("(()=>{const field=document.querySelector('[name=customer_name]');if(!field)return false;field.value='QA New Customer Without Selecting';const typed=field.type==='text'&&field.required&&field.maxLength===180&&field.checkValidity()&&!document.querySelector('select[name=customer]');field.value='';const blankBlocked=!field.checkValidity();field.value='محمد علی';return typed&&blankBlocked&&field.checkValidity()&&Boolean(document.getElementById(field.getAttribute('list')));})()");
  const checked=[];
  for(const kind of ['Sales Invoice','Payment Entry','Mobile Exchange','Warranty Claim','Mobile Intake']){
   await navigate(base+'/documents/'+encodeURIComponent(kind)+'/new/');
   const fieldName=kind==='Mobile Intake'?'trade_in_customer_name':'customer_name';
   const result=await evaluate("(()=>{const field=document.querySelector('[name="+fieldName+"]');if(!field)return false;field.value='QA New Customer Without Selecting';return field.type==='text'&&field.maxLength===180&&field.checkValidity()&&Boolean(document.getElementById(field.getAttribute('list')))&&!document.querySelector('select[name=customer],select[name=trade_in_customer]');})()");
   checked.push({kind,passed:result});
   if(kind==='Sales Invoice'){
    const height=await evaluate('Math.min(document.documentElement.scrollHeight,10000)');
    const screenshot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1365,height,scale:1}});
    fs.writeFileSync(path.join(output,'typed-customer-sales-form.png'),Buffer.from(screenshot.data,'base64'));
   }
  }
  customerEntryChecks.forms=checked;
  customerEntryChecks.passed=customerEntryChecks.pos&&checked.every(check=>check.passed);
  fs.writeFileSync(path.join(output,'customer-entry-checks.json'),JSON.stringify(customerEntryChecks,null,2));
  if(!customerEntryChecks.passed)process.exitCode=1;
 }
 let deviceChecks=null;
 if(process.argv.includes('--device-tests')){
  const waitFor=async expression=>{for(let i=0;i<100;i++){if(await evaluate(expression))return true;await pause(100);}return false;};
  await cdp('Network.enable');
  const appOrigin=new URL(base).origin;const externalRequests=[];const mockPosts=[];let mode='drop';
  requestInterceptor=async event=>{
   const request=event.request;
   if(new URL(request.url).pathname==='/api/pos/checkout'){
    mockPosts.push(request.postData);
    if(mode==='drop')await cdp('Fetch.failRequest',{requestId:event.requestId,errorReason:'Failed'});
    else await cdp('Fetch.fulfillRequest',{requestId:event.requestId,responseCode:mode==='validation'?400:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(mode==='validation'?{error:'QA validation rejected the draft'}:{id:'qa-mocked-no-invoice-created',url:'/device-check/?qa=recovered'})).toString('base64')});
   }else if(new URL(request.url).origin!==appOrigin){externalRequests.push(new URL(request.url).hostname);await cdp('Fetch.failRequest',{requestId:event.requestId,errorReason:'InternetDisconnected'});}
   else await cdp('Fetch.continueRequest',{requestId:event.requestId});
  };
  await cdp('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  await navigate(base+'/device-check/');
  const wanBlocked=await evaluate(`fetch('http://192.0.2.1/covison-wan-block-test').then(()=>false,()=>true)`);
  const serverProbe=await waitFor(`document.querySelector('[data-server-status]')?.dataset.reachable==='true'`);
  const scanTest=await evaluate(`(()=>{const field=document.getElementById('scanner-check');field.value='990000190000010';field.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));return document.getElementById('scanner-result').textContent.includes('check digit valid');})()`);
  const paperChecks=[];
  for(const [paper,width] of [['58',220],['80',303],['a4',794]]){
   await cdp('Emulation.setDeviceMetricsOverride',{width,height:1123,deviceScaleFactor:1,mobile:false});
   await navigate(base+'/device-check/print/?paper='+paper);await cdp('Emulation.setEmulatedMedia',{media:'print'});
   const dimensions=await evaluate(`(()=>({main:document.querySelector('main').getBoundingClientRect().width,ruler:document.querySelector('.test-ruler').getBoundingClientRect().width,printControlsHidden:getComputedStyle(document.querySelector('.receipt-toolbar')).display==='none',testMarked:document.body.innerText.includes('NOT A SALE')}))()`);
   const png=await cdp('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,'printer-test-'+paper+'.png'),Buffer.from(png.data,'base64'));
   paperChecks.push({paper,...dimensions,passed:dimensions.printControlsHidden&&dimensions.testMarked&&Math.abs(dimensions.ruler-40*96/25.4)<2&&dimensions.main<=width});
  }
  await cdp('Emulation.setEmulatedMedia',{media:''});await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:1000,deviceScaleFactor:1,mobile:false});
  const injection=await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`(()=>{const observer=new MutationObserver(()=>{const root=document.getElementById('pos');if(root){root.dataset.shiftOpen='true';observer.disconnect();}});observer.observe(document,{childList:true,subtree:true});})();`});
  await navigate(base+'/desk/point-of-sale');await waitFor(`window.covisonConnection?.reachable===true`);
  const prepare=()=>evaluate(`(()=>{const items=JSON.parse(document.getElementById('pos-data').textContent);const phone=items.find(p=>p.serialized);const field=document.getElementById('pos-search');field.value=phone.barcode.split(' ')[0];field.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));const form=document.getElementById('pos-cart');form.elements.customer_name.value='QA Typed Customer';for(const name of ['seller','handler']){const select=form.querySelector('[name='+name+']');select.value=Array.from(select.options).find(o=>o.value)?.value||'';}form.elements.discount_value.value='1.25';form.elements.discount_value.dispatchEvent(new Event('change'));return document.querySelectorAll('.cart-row').length===1;})()`);
  const exactScan=await prepare();
  await evaluate(`(()=>{const items=JSON.parse(document.getElementById('pos-data').textContent);const field=document.getElementById('pos-search');field.value=items.find(p=>p.serialized).barcode.split(' ')[0];field.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));})()`);
  const duplicateScan=await evaluate(`document.querySelectorAll('.cart-row').length===1&&document.querySelector('.cart-row input').value==='1'`);
  const prepareCash=()=>evaluate(`(()=>{const form=document.getElementById('pos-cart'),row=form.querySelector('[data-payment-rows] tr');row.querySelector('[name=payment_mode]').value='Cash';row.querySelector('[name=payment_amount]').value='4500.25';row.querySelector('[name=payment_tendered]').value='5000.50';window.covisonPayments.refresh(form);const correct=row.querySelector('[data-cash-change]').textContent.includes('500.25');form.querySelector('[data-add-payment]').click();const bank=form.querySelector('[data-payment-rows]').lastElementChild;const cleared=bank.querySelector('[name=payment_tendered]').value==='';bank.querySelector('[name=payment_mode]').value=Array.from(bank.querySelector('[name=payment_mode]').options).find(o=>o.value!=='Cash').value;bank.querySelector('[name=payment_amount]').value='1000';bank.querySelector('[name=payment_reference]').value='QA-BANK';window.covisonPayments.refresh(form);return {correct,cleared,bankDisabled:bank.querySelector('[name=payment_tendered]').disabled,bankHidden:bank.querySelector('[data-cash-input]').hidden,decimalExact:window.covisonPayments.cents('0.10')+window.covisonPayments.cents('0.20')===30};})()`);
  const cashPreview=await prepareCash();
  const cashHeight=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);
  const cashShot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1365,height:cashHeight,scale:1}});fs.writeFileSync(path.join(output,'pos-cash-change.png'),Buffer.from(cashShot.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});await pause(100);
  const cashMobile=await evaluate(`document.documentElement.scrollWidth<=innerWidth+2`);
  const mobileCashHeight=await evaluate(`Math.min(document.documentElement.scrollHeight,10000)`);
  const cashMobileShot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:390,height:mobileCashHeight,scale:1}});fs.writeFileSync(path.join(output,'pos-cash-change-mobile.png'),Buffer.from(cashMobileShot.data,'base64'));
  await cdp('Emulation.setDeviceMetricsOverride',{width:1365,height:900,deviceScaleFactor:1,mobile:false});
  const onlineInitially=await evaluate(`!document.querySelector('#pos-cart button.primary').disabled`);
  await cdp('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});await evaluate(`window.covisonConnection.check()`);
  await evaluate(`document.getElementById('pos-cart').requestSubmit()`);await pause(100);
  const offlineBlocked=await evaluate(`document.querySelector('#pos-cart button.primary').disabled&&document.querySelectorAll('.cart-row').length===1`);
  const noOfflinePost=mockPosts.length===0;
  await cdp('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});await evaluate(`window.covisonConnection.check()`);
  await evaluate(`document.getElementById('pos-cart').requestSubmit()`);
  const uncertainResult=await waitFor(`document.getElementById('pos-error').textContent.includes('uncertain')`);
  const frozenEditor=await evaluate(`document.querySelectorAll('.cart-row')[0].querySelectorAll('input')[1].disabled&&document.getElementById('pos-search').disabled&&document.querySelector('[name=discount_value]').disabled&&document.querySelector('[name=tax_rule]').disabled`);
  mode='success';await evaluate(`document.getElementById('pos-cart').requestSubmit()`);
  const retryRecovered=await waitFor(`location.pathname==='/device-check/'`);
  mode='validation';await navigate(base+'/desk/point-of-sale');await waitFor(`window.covisonConnection?.reachable===true`);await prepare();await prepareCash();await evaluate(`document.getElementById('pos-cart').requestSubmit()`);
  const validationShown=await waitFor(`document.getElementById('pos-error').textContent.includes('QA validation')`);
  const safeUnlock=await evaluate(`!document.querySelectorAll('.cart-row')[0].querySelectorAll('input')[1].disabled&&document.querySelectorAll('.cart-row')[0].querySelector('input').disabled`);
  const bankStillDisabled=await evaluate(`document.querySelector('[data-payment-rows]').lastElementChild.querySelector('[name=payment_tendered]').disabled`);
  await navigate(base+'/device-check/');await cdp('Page.removeScriptToEvaluateOnNewDocument',{identifier:injection.identifier});await cdp('Fetch.disable');requestInterceptor=null;
  deviceChecks={server_probe:serverProbe,scanner_input:scanTest,printer_layouts:paperChecks,exact_pos_scan:exactScan,serialized_duplicate_scan_blocked:duplicateScan,online_submit_available:onlineInitially,disconnected_submit_blocked:offlineBlocked&&noOfflinePost,uncertain_result_shown:uncertainResult,pending_editor_frozen:frozenEditor,retry_recovered:retryRecovered,retry_payload_identical:mockPosts.length>=2&&mockPosts[0]===mockPosts[1],validation_allows_edit:validationShown&&safeUnlock,mocked_pos_requests:mockPosts.length,real_pos_requests:0,wan_block_rule_verified:wanBlocked&&externalRequests.includes('192.0.2.1'),wan_requests_blocked:externalRequests,physical_hardware_tested:false};
  deviceChecks.passed=serverProbe&&scanTest&&paperChecks.every(p=>p.passed)&&exactScan&&duplicateScan&&onlineInitially&&offlineBlocked&&noOfflinePost&&uncertainResult&&frozenEditor&&retryRecovered&&mockPosts[0]===mockPosts[1]&&validationShown&&safeUnlock&&mockPosts.length===3&&deviceChecks.wan_block_rule_verified;
  const captured=JSON.parse(mockPosts[0]||'{}');const cashRetry=captured.payments?.[0]?.cash_tendered==='5000.50';const pricingRetry=captured.adjustments?.discount_type==='amount'&&captured.adjustments?.discount_value==='1.25'&&captured.adjustments?.tax_rule==='';
  Object.assign(deviceChecks,{cash_change_preview:cashPreview.correct,exact_paisa_arithmetic:cashPreview.decimalExact,split_row_cleared:cashPreview.cleared,bank_cash_hidden_disabled:cashPreview.bankDisabled&&cashPreview.bankHidden,bank_cash_disabled_after_validation:bankStillDisabled,cash_payload_retained:cashRetry,typed_customer_retained:captured.customer_name==='QA Typed Customer'&&!('customer' in captured),invoice_adjustments_retained:pricingRetry,cash_mobile_no_page_overflow:cashMobile});
  deviceChecks.passed=deviceChecks.passed&&cashPreview.correct&&cashPreview.decimalExact&&cashPreview.cleared&&cashPreview.bankDisabled&&cashPreview.bankHidden&&bankStillDisabled&&cashRetry&&cashMobile&&pricingRetry&&deviceChecks.typed_customer_retained;
  fs.writeFileSync(path.join(output,'device-checks.json'),JSON.stringify(deviceChecks,null,2));if(!deviceChecks.passed)process.exitCode=1;
 }
 await navigate(base+'/desk/mobile-shop');
 await evaluate(`(()=>{document.querySelector('form[action="/logout/"]').requestSubmit();return true;})()`);
 for(let i=0;i<100;i++){if(await evaluate('location.pathname')==='/login/'){signedIn=false;break;}await pause(100);}
 result={phase,headless_browser:true,existing_browser_profile_used:false,business_transactions_changed:false,customer_entry_checks:customerEntryChecks,report_checks:reportChecks,device_checks:deviceChecks,return_form_checks:returnFormChecks,fund_form_checks:fundFormChecks,journal_form_checks:journalFormChecks,screens,exceptions,report_filter_refresh_retained:filterRetained,pos_cart_interaction:posCartInteraction,pos_cart_details:posCartDetails,mobile_menu_interaction:mobileMenuInteraction,owner_session_signed_out:!signedIn};
 fs.writeFileSync(path.join(output,process.argv.includes('--interactions-only')?'interactions.json':'review.json'),JSON.stringify(result,null,2));
 if(phase==='after'&&(!filterRetained||!posCartInteraction||!mobileMenuInteraction||exceptions.length||screens.some(s=>s.pageOverflow||s.mobile?.pageOverflow||s.errorPage)))process.exitCode=1;
 console.log(JSON.stringify({phase,screens:screens.length,desktop_overflow:screens.filter(s=>s.pageOverflow).map(s=>s.path),mobile_overflow:screens.filter(s=>s.mobile?.pageOverflow).map(s=>s.path),page_errors:screens.filter(s=>s.errorPage).map(s=>s.path),javascript_exceptions:exceptions,report_filter_refresh_retained:filterRetained,pos_cart_interaction:posCartInteraction,pos_cart_details:posCartDetails,mobile_menu_interaction:mobileMenuInteraction,owner_session_signed_out:!signedIn},null,2));
}finally{
 socket?.close();chrome.kill('SIGTERM');
 await Promise.race([new Promise(r=>chrome.once('exit',r)),pause(3000)]);
 fs.rmSync(profile,{recursive:true,force:true,maxRetries:15,retryDelay:200});
 if(result.screens){result.temporary_browser_profile_removed=true;fs.writeFileSync(path.join(output,process.argv.includes('--interactions-only')?'interactions.json':'review.json'),JSON.stringify(result,null,2));}
}

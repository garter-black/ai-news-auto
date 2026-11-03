const { chromium, devices } = require('playwright');
const assert = require('node:assert/strict');
const adHosts = /(^|\.)(a8\.net|afl\.rakuten\.co\.jp|amazon\.co\.jp|amzn\.to|amazon-adsystem\.com|googlesyndication\.com|doubleclick\.net|googleadservices\.com|adtrafficquality\.google|adservice\.google\.com|cloudflareinsights\.com|google-analytics\.com|googletagmanager\.com)$/i;
(async()=>{
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 for (const mobile of [false,true]) {
  const context=await browser.newContext(mobile ? {...devices['iPhone 13'], reducedMotion:'reduce'} : { viewport:{width:1440,height:900}, reducedMotion:'reduce' });
  await context.route('**/*', route => {const host=new URL(route.request().url()).hostname; return adHosts.test(host) ? route.abort() : route.continue();});
  const page=await context.newPage();
  await page.goto(process.env.STORY_TEST_URL || 'http://127.0.0.1:3184/',{waitUntil:'domcontentloaded'});
  const rail=page.locator('.sr'); const next=page.getByRole('button',{name:'ストーリーを右に移動'}); const prev=page.getByRole('button',{name:'ストーリーを左に移動'});
  await rail.waitFor();
  if(mobile){
   // スマホは矢印なし・小さな丸を詰めて 4.5 個前後見せる（Instagram のストーリーのように横に続くと分かる）
   assert.equal(await next.isVisible(),false,'mobile: no arrows'); assert.equal(await prev.isVisible(),false,'mobile: no arrows');
   const {visible,size,label}=await page.evaluate(()=>{const r=document.querySelector('.sr').getBoundingClientRect();const rings=[...document.querySelectorAll('.sr-ring')];const w=rings.reduce((n,el)=>{const b=el.getBoundingClientRect();return n+Math.max(0,Math.min(b.right,r.right)-Math.max(b.left,r.left))/b.width},0);const img=document.querySelector('.sr-img').getBoundingClientRect();const l=getComputedStyle(document.querySelector('.sr-label'));return {visible:w,size:img.width,label:l.whiteSpace+'/'+l.textOverflow}});
   assert(visible>=4.3&&visible<=5.4,`mobile: visible rings ${visible}`); assert(size>=60&&size<=70,`mobile: ring ${size}`); assert.equal(label,'nowrap/ellipsis');
   await page.locator('.sr-ring').first().click(); await page.getByRole('dialog').waitFor(); assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden'); await page.getByRole('button',{name:'閉じる'}).click(); assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
   const box=await rail.boundingBox(); const cdp=await context.newCDPSession(page); const y=Math.round(box.y+40); await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:Math.round(box.x+box.width-50),y}]}); await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:Math.round(box.x+50),y}]}); await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]}); await page.waitForTimeout(300); assert((await rail.evaluate(e=>e.scrollLeft))>10,'mobile touch swipe');
   console.log(`mobile: ${visible.toFixed(1)} rings visible, no arrows, story open/close, touch swipe OK`);
   await context.close(); continue;
  }
  await next.waitFor(); assert.equal(await prev.getAttribute('aria-disabled'),'true');
  const initial=await rail.evaluate(e=>e.scrollLeft); await next.click(); await page.waitForTimeout(200); const moved=await rail.evaluate(e=>e.scrollLeft); assert(moved>initial+20, `${mobile}: right ${moved}`);
  for (let i=0;i<20 && await next.getAttribute('aria-disabled') !== 'true';i++) { await next.click(); await page.waitForTimeout(50); }
  assert.equal(await next.getAttribute('aria-disabled'),'true',`${mobile}: reached last story`);
  for (let i=0;i<20 && await prev.getAttribute('aria-disabled') !== 'true';i++) { await prev.click(); await page.waitForTimeout(50); }
  assert.equal(await prev.getAttribute('aria-disabled'),'true',`${mobile}: returned to first story`);
  await next.click(); await page.waitForTimeout(150);
  assert.equal(await prev.getAttribute('aria-disabled'),'false'); await prev.focus(); await page.keyboard.press('Enter'); await page.waitForTimeout(150); assert((await rail.evaluate(e=>e.scrollLeft))<moved,`${mobile}: keyboard left`);
  await page.locator('.sr-ring').first().click(); await page.getByRole('dialog').waitFor(); assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden'); await page.getByRole('button',{name:'閉じる'}).click(); assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
  console.log('desktop: arrows, keyboard, story open/close, body scroll OK');
  await context.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

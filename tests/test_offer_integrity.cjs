const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const sourcePath = path.join(root, 'lib/offers.ts');
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
const mod = new Module(sourcePath, module);mod.filename=sourcePath;mod.paths=Module._nodeModulePaths(path.dirname(sourcePath));const original=mod.require.bind(mod);mod.require=(name)=>name==='@/lib/content-fs'?fs:original(name);mod._compile(compiled,sourcePath);
const {offerForArticle,partnerItem}=mod.exports;
const offers=JSON.parse(fs.readFileSync(path.join(root,'content/offers.json'),'utf8'));
const links=JSON.parse(fs.readFileSync(path.join(root,'affiliate_links.json'),'utf8'));
const article={slug:'test-rtx',title:'RTX搭載のローカルAI用PC',seoTitle:'',tags:[],keywords:[],genreSlug:'ai',affiliateNames:[],verdict:null};
test('元の提携先URL・計測タグを保持し、期限をつける',()=>{
 const result=offerForArticle(article,12), item=result.items.find(i=>i.key==='oz-rtx5070ti');
 assert.ok(item);const p=offers.partners['oz-rtx5070ti'];const program=links[p.program];
 assert.equal(item.url,p.link??`${program.url}&a8ejpredirect=${encodeURIComponent(p.page)}`);
 assert.equal(item.image,p.image);assert.equal(item.activeUntil,'2026-11-01T00:00:00+09:00');
 const pixel=p.pixel??program.pixel;
 if(pixel) assert.ok(result.timedPixels.some(x=>x.url===pixel&&x.activeUntil===item.activeUntil)||result.pixels.includes(pixel));
});
test('販売停止中の候補をランキングと比較から外す',()=>{
 const result=offerForArticle(article,12);
 assert.equal(result.items.some(i=>i.key==='oz-rx9060xt'),false);
 assert.equal(partnerItem('oz-rx9060xt'),undefined);
 assert.ok(result.items.length>0);
});
test('クラウドゲームの記事にはローカルAI用のPCを勧めない',()=>{
 const cloud={...article,slug:'cloud',title:'NVIDIAのクラウドゲーム GeForce NOW に新作が登場'};
 assert.equal(offerForArticle(cloud,12),undefined);
 assert.ok(offerForArticle(article,12).items.some(i=>i.key==='oz-rtx5070ti'));
});

test('関連が特定できない記事に既定の広告を挿入しない',()=>{
 const unrelated={...article,slug:'unrelated',title:'宇宙望遠鏡の新しい観測',verdict:{audience:'business'}};
 assert.equal(offerForArticle(unrelated,12),undefined);
});
test('録音や議事録が主題の記事でのみボイスレコーダーを候補にする',()=>{
 const voice={...article,title:'AIで会議の議事録を文字起こし'};
 assert.ok(offerForArticle(voice,12).items.some(i=>i.key==='plaud-note-pro'));
 const chat={...article,title:'ChatGPT Voiceの会話機能が更新', affiliateNames:['PLAUD']};
 assert.ok(!offerForArticle(chat,12)?.items.some(i=>i.key==='plaud-note-pro'));
});
test('画像生成の料金と録音の無料枠を候補に引き継ぐ',()=>{
 const image={...article,title:'画像生成の新しい使い方'};
 const canvas=offerForArticle(image,12).items.find(i=>i.key==='conoha-ai-canvas');
 assert.match(canvas.priceText,/基本料/);assert.match(canvas.caution,/超える/);
 const voice={...article,title:'会議の議事録を文字起こし'};
 assert.match(offerForArticle(voice,12).items.find(i=>i.key==='plaud-note-pro').caution,/300分/);
});
test('動画タグより見出しの主題を優先する',()=>{
 const item={...article,title:'ChatGPTの音声会話が進化',tags:['YouTube']};
 assert.equal(offerForArticle(item,12).topic,'chatgpt');
});
test('記事の関連書籍は話題に合うタイトルだけ選ぶ',()=>{
 const voice={...article,title:'会議の議事録を文字起こし'};
 const books=offerForArticle(voice,12).items.filter(i=>i.kind==='book');
 assert.ok(books.length>0);
 assert.ok(books.every(i=>/議事録|文字起こし|音声|会議|録音|ボイス|transcri/i.test(i.name)));
});

// 未設定（YOUR_ID など）や計測IDの無いリンクは、どの枠にも出さない
const {isValidAffiliateUrl,offerPlanForArticle,fallbackOfferForArticle}=mod.exports;
function freshOffers(dir){
 const m=new Module(sourcePath,module);m.filename=sourcePath;m.paths=Module._nodeModulePaths(path.dirname(sourcePath));const req=m.require.bind(m);m.require=(name)=>name==='@/lib/content-fs'?fs:req(name);m._compile(compiled,sourcePath);
 const cwd=process.cwd();
 return (fn)=>{process.chdir(dir);try{return fn(m.exports);}finally{process.chdir(cwd);}};
}
test('提携リンクの形を確かめる（A8・主要ASPだけ、プレースホルダは不可）',()=>{
 for(const ok of ['https://px.a8.net/svt/ejp?a8mat=4BEPPU+DBVECY+3XCC+6AZAQ','https://px.a8.net/svt/ejp?a8mat=4BEPPU+DBVECY+3XCC+6AZAQ&a8ejpredirect=https%3A%2F%2Fwww.matsui.co.jp%2F','https://rpx.a8.net/svt/ejp?a8mat=4BELTP+50ED2Q+2HOM+BWGDT&rakuten=y&a8ejpredirect=https%3A%2F%2Fhb.afl.rakuten.co.jp%2Fhgc%2Fx']) assert.equal(isValidAffiliateUrl(ok),true,ok);
 for(const ng of ['https://www.rakuten-sec.co.jp/?ref=YOUR_ID','https://px.a8.net/svt/ejp?a8mat=YOUR_ID','https://px.a8.net/svt/ejp?a8mat=4BEPPU+DBVECY+3XCC+6AZAQ&a8ejpredirect=https%3A%2F%2Fexample.com%2F','https://px.a8.net/svt/ejp?a8mat=','https://px.a8.net/svt/ejp','http://px.a8.net/svt/ejp?a8mat=4BEPPU+DBVECY+3XCC+6AZAQ','https://www.sbisec.co.jp/','https://px.a8.net.evil.example/svt/ejp?a8mat=4BEPPU+DBVECY+3XCC+6AZAQ','javascript:alert(1)','',null,undefined]) assert.equal(isValidAffiliateUrl(ng),false,String(ng));
});
test('登録済みの提携商品と本はすべて正しい提携リンクで出る',()=>{
 for(const [key,p] of Object.entries(offers.partners)){
  const item=partnerItem(key);
  if(p.hidden){assert.equal(item,undefined,key);continue;}
  assert.ok(item,key);assert.ok(isValidAffiliateUrl(item.item.url),key);
 }
 for(const genreSlug of ['ai','business','investment','real-estate','public','sports','tech-gadget','entertainment','lifestyle','science']){
  const plan=offerPlanForArticle({...article,slug:`g-${genreSlug}`,title:'褒章の受章者が決定',genreSlug});
  for(const item of [...(plan.rail?.items??[]),...(plan.inline?.items??[])]) assert.ok(isValidAffiliateUrl(item.url),`${genreSlug} ${item.key}`);
 }
});
test('プレースホルダの残った提携先（楽天証券 YOUR_ID など）は広告に出さない',()=>{
 const dir=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'offers-'));
 fs.mkdirSync(path.join(dir,'content'));
 const fixture=JSON.parse(JSON.stringify(offers));
 fixture.partners['rakuten-sec-test']={program:'楽天証券',name:'楽天証券',label:'楽天証券',price:null,image:null,page:null,shop:'楽天証券'};
 fixture.partners['bad-link-test']={program:'松井証券',name:'壊れた素材',label:'壊れた素材',price:null,image:null,page:null,shop:'A8',link:'https://px.a8.net/svt/ejp?a8mat=YOUR_ID',pixel:'https://www10.a8.net/0.gif?a8mat=YOUR_ID'};
 fixture.partners['no-id-test']={program:'公式だけ',name:'計測なし',label:'計測なし',price:null,image:null,page:null,shop:'公式'};
 const topic=fixture.topics.find(t=>(fixture.fallbackByGenre?.business??'')===t.key)??fixture.topics[0];
 for(const t of fixture.topics) if(/nisa|invest|sec/i.test(t.key)) t.partners=['rakuten-sec-test','bad-link-test','no-id-test',...t.partners];
 fixture.articleFallback.sets.money.partners=['rakuten-sec-test','bad-link-test','no-id-test',...fixture.articleFallback.sets.money.partners];
 const affLinks={...links,'公式だけ':{url:'https://www.example-sec.co.jp/'}};
 assert.match(affLinks['楽天証券'].url,/YOUR_ID/);
 fs.writeFileSync(path.join(dir,'content/offers.json'),JSON.stringify(fixture));
 fs.copyFileSync(path.join(root,'content/products.json'),path.join(dir,'content/products.json'));
 fs.writeFileSync(path.join(dir,'affiliate_links.json'),JSON.stringify(affLinks));
 const run=freshOffers(dir);
 run(({partnerItem,offerPlanForArticle,fallbackOfferForArticle,isValidAffiliateUrl})=>{
  for(const key of ['rakuten-sec-test','bad-link-test','no-id-test']) assert.equal(partnerItem(key),undefined,key);
  const items=[];const pixels=[];
  for(const a of [{...article,slug:'nisa',title:'楽天証券の新NISA口座が1,500万を突破',genreSlug:'business'},{...article,slug:'nisa2',title:'新NISAの積立投資',genreSlug:'investment'},{...article,slug:'fb',title:'褒章の受章者が決定',genreSlug:'investment'}]){
   const plan=offerPlanForArticle(a);
   for(const o of [plan.offer,plan.rail,plan.inline].filter(Boolean)){items.push(...o.items);pixels.push(...o.pixels,...(o.timedPixels??[]).map(p=>p.url));}
  }
  const fb=fallbackOfferForArticle({...article,slug:'fb2',title:'褒章',genreSlug:'investment'});
  items.push(...fb.items);pixels.push(...fb.pixels);
  assert.ok(items.length>0);
  assert.ok(items.every(i=>!['rakuten-sec-test','bad-link-test','no-id-test'].includes(i.key)));
  assert.ok(items.every(i=>isValidAffiliateUrl(i.url)));
  assert.ok(pixels.every(p=>!/YOUR_ID/.test(p)));
 });
 fs.rmSync(dir,{recursive:true,force:true});
});

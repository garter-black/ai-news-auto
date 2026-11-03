const assert=require('node:assert/strict');const fs=require('node:fs');const Module=require('node:module');const path=require('node:path');const test=require('node:test');const ts=require('typescript');
const root=path.resolve(__dirname,'..');const file=path.join(root,'lib/stories.ts');
const NOW=Date.parse('2026-11-02T12:00:00Z');const H=3_600_000;
const iso=(hoursAgo)=>new Date(NOW-hoursAgo*H).toISOString();
function article(slug,title,{hours=1,genre='ai',audience='一般ユーザー',tags=[],company='google',...rest}={}){return {slug,title,tags,keywords:[],genreSlug:genre,genre,type:'news',hasEasy:true,generatedAt:iso(hours),publishedAt:iso(hours),highlights:[],thumbnail:'',lede:title,company,verdict:{status:'available',japan:'ok',free:true,audience},...rest}}
let items=[];
const COMPANIES=[{slug:'openai',name:'OpenAI',color:'#0a0'},{slug:'google',name:'Google',color:'#00f'}];
// ブラウザ側の当て直し（静的ページが長くキャッシュされても、閲覧時刻で24時間を数える）
const winFile=path.join(root,'lib/story-window.ts');
const win=new Module(winFile,module);win.filename=winFile;win._compile(ts.transpileModule(fs.readFileSync(winFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,winFile);
const mocks={
 '@/lib/articles':{getAllArticles:()=>items,coverPath:()=>'/cover.png'},
 '@/lib/companies':{COMPANIES,companyOf:(a)=>COMPANIES.find(c=>c.slug===a.company)||{slug:'other',name:'その他',color:'#000'}},
 '@/lib/general':{easyGist:(a)=>a.lede},
 '@/lib/sections':{BROAD_SECTIONS:[{slug:'business',label:'ビジネス・経済'}]},
 '@/lib/story-window':win.exports,
 // AIの話題の判定は本物（lib/index-rules.json。scripts/indexnow.py が書き出す）
 '@/lib/indexing':(()=>{const r=JSON.parse(fs.readFileSync(path.join(root,'lib/index-rules.json'),'utf8'));return {AI_TOPIC:new RegExp(r.aiTopic.source,r.aiTopic.flags)}})(),
 '@/lib/verdict':{freeLabel:()=>'',japanLabel:()=>'',usability:(v)=>v.status==='available'?'go':'wait',listUsability:(a)=>a.verdict?(a.verdict.status==='available'?'go':'wait'):undefined,verdictOf:(a)=>a.verdict??undefined,isProductNews:(a)=>!/決算|統計|取り組み|啓発/.test(a.title),USABILITY_LABEL:{go:'使える',wait:'まだ'}}
};
const source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=(name)=>mocks[name]||orig(name);m._compile(source,file);
const bundles=()=>m.exports.getStoryBundles(NOW);
const find=(key)=>bundles().find(b=>b.key===key);

test('総合ニュースに重なっても勉強テーマを表示（2件でも出す）',()=>{
 items=[article('notebook','Googleの学習AIに無料の教科書'),article('students','Geminiで学生の勉強を助ける',{hours:2}),article('other','AIニュースの別の話',{hours:3})];
 const study=find('study');assert.ok(study);assert.deepEqual(study.items.map(a=>a.slug),['notebook','students']);assert.equal(study.label,'勉強に使えるAI');
});
test('AIモデルの学習効率だけの記事を勉強向けに混ぜない',()=>{
 items.push(article('training','AIモデル学習効率の最適化'));assert.ok(!find('study').items.some(x=>x.slug==='training'));
});
test('24時間を過ぎた記事は丸から外れ、0本の丸は出ない',()=>{
 items=[article('new','ChatGPTの新機能',{company:'openai',hours:2}),article('old','Geminiで勉強する方法',{hours:30})];
 assert.deepEqual(find('today').items.map(a=>a.slug),['new']);
 assert.equal(find('study'),undefined);assert.equal(find('google'),undefined);
 assert.ok(bundles().every(b=>b.items.length>0));
});
test('本数に上限をかけない（12本で打ち切らない）',()=>{
 items=Array.from({length:20},(_,i)=>article(`a${i}`,`AIの話${i}`,{hours:i*0.5}));
 assert.equal(find('today').items.length,20);assert.equal(find('google').items.length,20);
});
test('企業の丸は「30日」ではなく名前だけ、新しい記事がある丸から並ぶ',()=>{
 items=[article('g','Geminiの発表',{hours:5}),article('o','ChatGPTの発表',{company:'openai',hours:1}),article('b','日銀が金利を発表',{genre:'business',hours:3,verdict:undefined})];
 const list=bundles();
 assert.ok(!list.some(b=>/30日/.test(b.label)));
 assert.equal(find('openai').label,'OpenAI');
 for(const tier of [1,2]){const t=list.filter(b=>b.tier===tier).map(b=>Date.parse(b.items[0].at));assert.deepEqual(t,[...t].sort((x,y)=>y-x));}
 assert.ok(list.findIndex(b=>b.key==='openai')<list.findIndex(b=>b.key==='money'));
 assert.ok(list.findIndex(b=>b.key==='money')<list.findIndex(b=>b.key==='google'));
});
test('最新のAIニュースが先頭、次は読者の関心の中心の段、開発者向けなどはその後（段の中は新しい順）',()=>{
 items=[
  article('dev','開発者向けの新しいSDKを公開',{hours:0.5,audience:'開発者'}),
  article('o','ChatGPTの料金が改定',{company:'openai',hours:3,verdict:{status:'soon',japan:'ok',free:false,audience:'一般ユーザー'}}),
  article('b','日銀が金利を発表',{genre:'business',hours:2,verdict:undefined}),
 ];
 const list=bundles();const keys=list.map(b=>b.key);
 assert.equal(keys[0],'today');
 assert.deepEqual(keys.filter(k=>k!=='google'),['today','money','g-business','openai','dev']);
 assert.ok(keys.indexOf('google')<keys.indexOf('dev'));
 assert.deepEqual(list.map(b=>b.tier),[0,1,1,1,1,2]);
 const later=win.exports.liveBundles(list,NOW+1*H);
 assert.deepEqual(later.map(b=>b.key),keys);
});
test('仕事の丸は見出しで判定し、開発者・情シス向けの記事は入れない',()=>{
 items=[
  article('excel','Copilotで会議の議事録を自動作成',{audience:'ビジネスパーソン'}),
  article('infra','オラクルの業務システムをChatGPTから操作',{audience:'企業の情シス'}),
  article('tag','新しいAIモデルを発表',{tags:['業務自動化','DX']}),
 ];
 assert.deepEqual(find('work').items.map(a=>a.slug),['excel']);
});
test('仕事とお金の丸は家計・働き方に関わる見出しだけ（AI記事・企業の統計は入れない）',()=>{
 items=[
  article('rate','10年国債の金利が上昇、住宅ローンへの影響',{genre:'business',verdict:undefined}),
  article('buyback','NTTが自社株買いを実施',{genre:'business',verdict:undefined,tags:['金融','株式']}),
  article('aimoney','AIで資産管理する新サービス'),
 ];
 assert.deepEqual(find('money').items.map(a=>a.slug),['rate']);
 assert.deepEqual(find('g-business').items.map(a=>a.slug).sort(),['buyback','rate']);
});
test('続報の丸は続報の時刻で24時間を数える',()=>{
 items=[article('fu','前からの話',{hours:60,updatedAt:iso(3)}),article('stale','古い続報',{hours:80,updatedAt:iso(40)})];
 assert.deepEqual(find('followup').items.map(a=>a.slug),['fu']);
 assert.equal(find('today'),undefined);
});
test('重複記事は丸に入れない',()=>{
 items=[article('main','Gemini 4 発表'),article('dup','Gemini 4 発表（別ソース）',{duplicate:true})];
 assert.deepEqual(find('today').items.map(a=>a.slug),['main']);
});

test('閲覧時に24時間を過ぎた記事を外し、空の丸を消して新しい順に並べ直す',()=>{
 items=[article('o1','ChatGPTの発表',{company:'openai',hours:20,thumbnail:'/o1.jpg'}),article('g1','Geminiの発表',{hours:10,thumbnail:'/g1.jpg'}),article('g2','Geminiの続き',{hours:2,thumbnail:'/g2.jpg'})];
 const built=bundles();
 const later=win.exports.liveBundles(built,NOW+6*H);
 assert.equal(later.find(b=>b.key==='openai'),undefined);
 const google=later.find(b=>b.key==='google');assert.deepEqual(google.items.map(a=>a.slug),['g2','g1']);
 assert.ok(google.items.some(it=>it.image===google.cover));
 assert.equal(later[0].key,'today');
 assert.deepEqual(win.exports.liveBundles(built,NOW+30*H),[]);
});

test('先頭の丸たちの表紙に同じ記事を使わない（AIでない記事・製品でない記事は「今使える」「無料で試せる」に入れない。点検13）',()=>{
 items=[
  article('samba','kubell、法人向けストレージ「セキュアSAMBA」にMac用アプリ',{hours:0.5,company:'other'}),
  article('grid','OpenAI、電力網を守る取り組み「Grid Protectors」',{hours:1,company:'openai',verdict:{status:'soon',japan:'unknown',free:null,audience:'電力会社'}}),
 ];
 assert.equal(find('go'),undefined,'AIでない記事で「今使えるAI新機能」の丸を作っている');
 assert.equal(find('free'),undefined,'AIでない記事で「無料で試せるAI」の丸を作っている');
 items.push(article('gemini','Geminiに無料の新機能',{hours:2}),article('claude','Claudeの新機能が無料で使える',{hours:3,company:'other'}));
 const list=bundles();
 assert.ok(find('go')&&find('free'));
 assert.ok(!find('go').items.some(x=>x.slug==='samba'));
});
test('「今使える」「無料で試せる」は、ほかの丸と同じ記事を表紙にするなら出さない（先頭の3つの丸が同じ記事にならない）',()=>{
 items=[article('only','Geminiに無料の新機能',{hours:1,thumbnail:'/a.jpg'})];
 const keys=bundles().map(b=>b.key);
 assert.equal(keys[0],'today');
 assert.ok(!keys.includes('go')&&!keys.includes('free'),`同じ1本の記事を表紙にした丸がある: ${keys}`);
 items.push(article('two','ChatGPTに無料の新機能',{hours:2,company:'openai',thumbnail:'/b.jpg'}),article('three','Claudeの新機能を無料で提供',{hours:3,company:'other',thumbnail:'/c.jpg'}));
 const list=bundles();
 const lead=['today','go','free'].map(k=>list.find(b=>b.key===k)).filter(Boolean).map(b=>b.cover);
 assert.equal(new Set(lead).size,lead.length,`先頭の丸の表紙が重なっている: ${lead}`);
});

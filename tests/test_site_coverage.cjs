// サイトの自己紹介は実際に扱っている中身どおりに（2026-11-03 オーナー決定・台帳 W14。lib/coverage.ts）
// 1. 説明文（site.config.ts の description・トップ・運営者情報・編集方針・新着の説明）に、記事の少ないジャンル名を決め打ちで書かない
// 2. ジャンル名は、直近30日に5本以上の記事があるものだけ出す（増えれば載り、止まれば消える）
const assert=require('node:assert/strict');const fs=require('node:fs');const Module=require('node:module');const path=require('node:path');const test=require('node:test');const ts=require('typescript');
const root=path.resolve(__dirname,'..');const file=path.join(root,'lib/coverage.ts');
const NOW=Date.parse('2026-11-03T12:00:00Z');const DAY=86_400_000;
let items=[];
const LABELS={business:'ビジネス・経済',investment:'投資・マネー','real-estate':'不動産',public:'社会・行政','tech-gadget':'テクノロジー・ガジェット',sports:'スポーツ',lifestyle:'暮らし・生活',entertainment:'エンタメ・カルチャー'};
const mocks={
 '@/lib/articles':{getAllArticles:()=>items},
 '@/lib/nav':{GENRE_NAV_ORDER:['ai','business','investment','real-estate','public','tech-gadget','sports','lifestyle','entertainment'],genreLabel:(s)=>LABELS[s]||s},
};
const source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=(name)=>mocks[name]||orig(name);m._compile(source,file);
const make=(genre,n,daysAgo=1)=>Array.from({length:n},(_,i)=>({slug:`${genre}-${daysAgo}-${i}`,genreSlug:genre,type:'news',generatedAt:new Date(NOW-daysAgo*DAY).toISOString()}));

test('定期的に記事があるジャンルだけ名前を出す（AI は軸なので出さない）',()=>{
 items=[...make('ai',50),...make('business',47),...make('investment',1),...make('entertainment',1),...make('public',7),...make('sports',4)];
 assert.deepEqual(m.exports.regularGenres(items,NOW).map(g=>g.slug),['business','public']);
 assert.equal(m.exports.alsoCoveredSentence(items,NOW),'このほか、ビジネス・経済、社会・行政のニュースも扱います。');
});
test('記事が増えれば名前が載り、古い記事だけなら消える',()=>{
 items=[...make('investment',5),...make('real-estate',6,10),...make('entertainment',9,40)];
 assert.deepEqual(m.exports.regularGenres(items,NOW).map(g=>g.slug),['investment','real-estate']);
 items=[];assert.equal(m.exports.alsoCoveredSentence(items,NOW),'');
});
test('説明文にジャンル名を決め打ちで並べない（不動産・投資は記事が定期的にあるときだけ lib/coverage.ts が足す）',()=>{
 const config=fs.readFileSync(path.join(root,'site.config.ts'),'utf8');
 const desc=config.match(/description:\s*\n?\s*"([^"]+)"/)[1];
 assert.match(desc,/AI・テクノロジーを軸に、仕事とお金に関わるニュース/);
 assert.doesNotMatch(desc,/不動産|投資|エンタメ/);
 for(const rel of ['app/page.tsx','app/about/page.tsx','app/editorial-policy/page.tsx','app/articles/view.tsx']){
  const src=fs.readFileSync(path.join(root,rel),'utf8');
  assert.doesNotMatch(src,/AI・ビジネス・お金・投資・不動産/,rel);
  assert.doesNotMatch(src,/扱うのは、AI・テック、ビジネス・お金・投資、不動産/,rel);
 }
 for(const rel of ['app/page.tsx','app/about/page.tsx','app/editorial-policy/page.tsx']){
  assert.match(fs.readFileSync(path.join(root,rel),'utf8'),/alsoCoveredSentence\(\)/,rel);
 }
});

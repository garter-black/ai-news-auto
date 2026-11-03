/**
 * 編集部（オーナー）の記事確認ページ。cloudflare/worker.ts が /review と /review/api/* をここに回す（Next を通さない）。
 * 毎日の義務ではない。読んだ記事にだけ「確認済み」を付ける軽い仕組み（2026-11-03）。
 *
 *   GET  /review                 ページ（ログインしていなければ、確認コードを受け取る画面）
 *   POST /review/api/code        確認コードを、決めたメールアドレス（secret REVIEW_OWNER_EMAIL）にだけ送る
 *   POST /review/api/login       確認コード（6桁）を入れるとログイン（Cookie。URL に秘密を入れない）
 *   POST /review/api/logout
 *   GET  /review/api/list        最近の記事と確認の状態
 *   POST /review/api/mark        { slug, status: "ok" | "fix" | "clear", note? }
 *
 * 保存先は Workers KV の REVIEW_KV（saisoku-review）。デプロイしても消えない:
 *   r:<slug>      確認の記録 { status, at, note, title }（metadata に status・at）。消さない
 *   s:<乱数>      ログイン中のしるし（30日で消える）
 *   code          確認コードのハッシュと試した回数（10分で消える）
 *   rl:*          送りすぎ・試しすぎの制限
 * 記事ページの「編集部が確認済み」の表示は、.github/workflows/review_sync.yml（scripts/review_sync.py）が
 * この記録を記事 JSON の review に書き写してから出る（15〜30分ほど）。
 */
import { json, readJson, safeEqual, sha256Hex, type KvNamespace } from "./push";

export type ReviewEnv = {
  REVIEW_KV?: KvNamespace;
  REVIEW_OWNER_EMAIL?: string;
  RESEND_API_KEY?: string;
  NEWSLETTER_FROM?: string;
  ASSETS?: { fetch: (input: string | Request) => Promise<Response> };
};

export type ReviewStatus = "ok" | "fix";
export type ReviewRecord = { status: ReviewStatus; at: string; note?: string; title?: string; issue?: number; syncedAt?: string };
type ListItem = { slug: string; title: string; genre: string; generatedAt: string; breaking?: boolean; sns?: boolean };

const COOKIE = "__Host-saisoku_review";
const SESSION_DAYS = 30;
const CODE_TTL = 600;
const CODE_TRIES = 5;
const SEND_GAP_SEC = 60;
const SEND_PER_DAY = 10;
const FAIL_PER_DAY = 30;
const SLUG = /^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$/;
const NOTE_MAX = 300;

const JST = 9 * 3600_000;
const jstDate = (ms = Date.now()) => new Date(ms + JST).toISOString().slice(0, 10);

const SECURITY_HEADERS: Record<string, string> = {
  "Cache-Control": "no-store, private",
  "X-Robots-Tag": "noindex, nofollow",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

function withHeaders(response: Response, extra: Record<string, string> = {}): Response {
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries({ ...SECURITY_HEADERS, ...extra })) out.headers.set(k, v);
  return out;
}

/** 同じサイトの画面からの POST だけ受ける（CSRF 対策。Cookie は SameSite=Lax） */
function trustedPost(request: Request): boolean {
  const origin = request.headers.get("origin");
  return request.method === "POST" && !!origin && origin === new URL(request.url).origin && request.headers.get("x-saisoku-review") === "1";
}

function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sixDigits(): string {
  const buf = new Uint32Array(1);
  // 偏りが出ないよう、1,000,000 の倍数に収まる値だけを使う
  const limit = Math.floor(0x1_0000_0000 / 1_000_000) * 1_000_000;
  do crypto.getRandomValues(buf);
  while (buf[0] >= limit);
  return String(buf[0] % 1_000_000).padStart(6, "0");
}

function cookieValue(request: Request): string {
  const raw = request.headers.get("cookie") ?? "";
  for (const part of raw.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === COOKIE) return rest.join("=");
  }
  return "";
}

async function session(request: Request, kv: KvNamespace): Promise<boolean> {
  const id = cookieValue(request);
  if (!/^[0-9a-f]{64}$/.test(id)) return false;
  return (await kv.get(`s:${await sha256Hex(id)}`)) !== null;
}

async function bump(kv: KvNamespace, key: string, ttl: number): Promise<number> {
  const n = Number((await kv.get(key)) ?? "0") + 1;
  await kv.put(key, String(n), { expirationTtl: ttl });
  return n;
}

async function sendCode(env: ReviewEnv, code: string): Promise<boolean> {
  if (!env.RESEND_API_KEY || !env.REVIEW_OWNER_EMAIL) return false;
  const from = env.NEWSLETTER_FROM || "news@ai-tech-express.jp";
  const text = `サイソクニュースの記事確認ページの確認コードです。\n\n${code}\n\n10分以内に、確認ページの入力欄に入れてください。\n心当たりがない場合は、このメールを無視してください（コードは10分で使えなくなります）。`;
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: `サイソクニュース <${from}>`,
      to: [env.REVIEW_OWNER_EMAIL],
      subject: `記事確認ページの確認コード：${code}`,
      text,
    }),
  });
  if (!res.ok) console.error("[review] mail failed", res.status, (await res.text()).slice(0, 200));
  return res.ok;
}

async function handleCode(request: Request, env: ReviewEnv, kv: KvNamespace): Promise<Response> {
  if (await kv.get("rl:gap")) return json({ error: "少し待ってから、もう一度押してください（1分に1回まで）。" }, 429);
  const dayKey = `rl:send:${jstDate()}`;
  if (Number((await kv.get(dayKey)) ?? "0") >= SEND_PER_DAY) return json({ error: "今日はこれ以上送れません。明日またお試しください。" }, 429);
  const code = sixDigits();
  await kv.put("code", JSON.stringify({ hash: await sha256Hex(code), tries: 0, exp: Date.now() + CODE_TTL * 1000 }), { expirationTtl: CODE_TTL });
  await kv.put("rl:gap", "1", { expirationTtl: SEND_GAP_SEC });
  await bump(kv, dayKey, 2 * 86400);
  if (!(await sendCode(env, code))) return json({ error: "メールを送れませんでした。時間をおいてお試しください。" }, 502);
  return json({ sent: true });
}

async function handleLogin(request: Request, kv: KvNamespace): Promise<Response> {
  const failKey = `rl:fail:${jstDate()}`;
  if (Number((await kv.get(failKey)) ?? "0") >= FAIL_PER_DAY) return json({ error: "今日は試せる回数を超えました。明日またお試しください。" }, 429);
  const body = await readJson<{ code?: unknown }>(request, 200);
  const code = typeof body?.code === "string" ? body.code.replace(/\s/g, "") : "";
  const raw = await kv.get("code");
  if (!raw) return json({ error: "確認コードの期限が切れました。もう一度コードを受け取ってください。" }, 400);
  const saved = JSON.parse(raw) as { hash: string; tries: number; exp: number };
  if (!/^\d{6}$/.test(code) || !safeEqual(await sha256Hex(code), saved.hash)) {
    await bump(kv, failKey, 2 * 86400);
    const tries = saved.tries + 1;
    const ttl = Math.floor((saved.exp - Date.now()) / 1000);
    if (tries >= CODE_TRIES || ttl < 60) {
      await kv.delete("code");
      return json({ error: "確認コードが違います。もう一度コードを受け取ってください。" }, 400);
    }
    await kv.put("code", JSON.stringify({ ...saved, tries }), { expirationTtl: ttl });
    return json({ error: `確認コードが違います（あと${CODE_TRIES - tries}回）。` }, 400);
  }
  await kv.delete("code");
  const id = randomHex(32);
  await kv.put(`s:${await sha256Hex(id)}`, JSON.stringify({ at: new Date().toISOString() }), { expirationTtl: SESSION_DAYS * 86400 });
  return withHeaders(json({ ok: true }), {
    "Set-Cookie": `${COOKIE}=${id}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}`,
  });
}

async function handleLogout(request: Request, kv: KvNamespace): Promise<Response> {
  const id = cookieValue(request);
  if (/^[0-9a-f]{64}$/.test(id)) await kv.delete(`s:${await sha256Hex(id)}`);
  return withHeaders(json({ ok: true }), { "Set-Cookie": `${COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0` });
}

async function recentArticles(env: ReviewEnv): Promise<ListItem[]> {
  if (!env.ASSETS) return [];
  const res = await env.ASSETS.fetch(new Request("https://assets.local/_content/review.json"));
  if (!res.ok) return [];
  return ((await res.json()) as { articles?: ListItem[] }).articles ?? [];
}

async function allRecords(kv: KvNamespace): Promise<Record<string, { status: ReviewStatus; at: string }>> {
  const out: Record<string, { status: ReviewStatus; at: string }> = {};
  let cursor: string | undefined;
  for (let i = 0; i < 20; i++) {
    const page = await kv.list({ prefix: "r:", cursor, limit: 1000 });
    for (const key of page.keys) {
      const meta = key.metadata as { status?: ReviewStatus; at?: string } | undefined;
      if (meta?.status && meta.at) out[key.name.slice(2)] = { status: meta.status, at: meta.at };
    }
    if (page.list_complete || !page.cursor) break;
    cursor = page.cursor;
  }
  return out;
}

async function handleList(env: ReviewEnv, kv: KvNamespace): Promise<Response> {
  const [articles, records] = await Promise.all([recentArticles(env), allRecords(kv)]);
  return json({ articles, records });
}

async function handleMark(request: Request, env: ReviewEnv, kv: KvNamespace): Promise<Response> {
  const body = await readJson<{ slug?: unknown; status?: unknown; note?: unknown }>(request, 2000);
  const slug = typeof body?.slug === "string" ? body.slug : "";
  const status = body?.status;
  if (!SLUG.test(slug) || (status !== "ok" && status !== "fix" && status !== "clear")) return json({ error: "記事か操作が正しくありません。" }, 400);
  const article = (await recentArticles(env)).find((a) => a.slug === slug);
  if (!article) return json({ error: "この記事は一覧にありません（古い記事か、取り下げた記事です）。" }, 404);
  const note = typeof body?.note === "string" ? body.note.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, NOTE_MAX) : "";
  const at = new Date().toISOString();
  if (status === "clear") {
    // 取り消しも記録として残す（記事 JSON の review を外すため。scripts/review_sync.py が読む）
    await kv.put(`r:${slug}`, JSON.stringify({ status: "clear", at, title: article.title }), { metadata: { status: "clear", at } });
    return json({ ok: true, slug, status, at });
  }
  const record: ReviewRecord = { status, at, title: article.title, ...(note ? { note } : {}) };
  await kv.put(`r:${slug}`, JSON.stringify(record), { metadata: { status, at } });
  return json({ ok: true, slug, status, at });
}

export async function handleReview(request: Request, url: URL, env: ReviewEnv): Promise<Response> {
  const kv = env.REVIEW_KV;
  if (!kv) return withHeaders(new Response("Not configured", { status: 503 }));
  if (url.pathname === "/review" || url.pathname === "/review/") {
    if (request.method !== "GET" && request.method !== "HEAD") return withHeaders(new Response("Method Not Allowed", { status: 405 }));
    const nonce = randomHex(16);
    const authed = await session(request, kv);
    return withHeaders(new Response(page(authed, nonce, Boolean(env.REVIEW_OWNER_EMAIL && env.RESEND_API_KEY)), { headers: { "Content-Type": "text/html; charset=utf-8" } }), {
      "Content-Security-Policy": `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; connect-src 'self'; img-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'`,
    });
  }
  if (!url.pathname.startsWith("/review/api/")) return withHeaders(new Response("Not Found", { status: 404 }));
  const action = url.pathname.slice("/review/api/".length);
  if (action === "list") {
    if (request.method !== "GET") return withHeaders(json({ error: "method" }, 405));
    if (!(await session(request, kv))) return withHeaders(json({ error: "ログインしてください。" }, 401));
    return withHeaders(await handleList(env, kv));
  }
  if (!trustedPost(request)) return withHeaders(json({ error: "forbidden" }, 403));
  if (action === "code") return withHeaders(await handleCode(request, env, kv));
  if (action === "login") return withHeaders(await handleLogin(request, kv));
  if (action === "logout") return handleLogout(request, kv);
  if (action === "mark") {
    if (!(await session(request, kv))) return withHeaders(json({ error: "ログインしてください。" }, 401));
    return withHeaders(await handleMark(request, env, kv));
  }
  return withHeaders(json({ error: "not found" }, 404));
}

// ---- 画面（スマホ向け。外部の読み込みなし） ----------------------------------------

function page(authed: boolean, nonce: string, mailReady: boolean): string {
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>記事の確認｜サイソク編集部</title>
<style nonce="${nonce}">
:root{--ink:#0b1a33;--navy:#1d4a8f;--red:#c8221a;--bg:#f3f5f8;--line:#d5dbe5;--muted:#5b6b82;--ok:#17703c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
main{max-width:720px;margin:0 auto;padding:16px 16px 64px}h1{font-size:20px;margin:8px 0 4px}p{margin:8px 0}.muted{color:var(--muted);font-size:14px}
button{font:inherit;border-radius:10px;border:1px solid var(--navy);padding:10px 14px;min-height:44px;background:#fff;color:var(--navy);font-weight:700}
button.primary{background:var(--navy);color:#fff}button.warn{border-color:var(--red);color:var(--red)}button:disabled{opacity:.5}
input,textarea{font:inherit;width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#fff}
.tabs{display:flex;gap:8px;margin:12px 0;flex-wrap:wrap}.tabs button{padding:6px 12px;min-height:36px;font-size:14px}.tabs button[aria-pressed=true]{background:var(--navy);color:#fff}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0}
.card a{color:var(--ink);font-weight:700;text-decoration:underline;text-underline-offset:3px}.meta{font-size:13px;color:var(--muted);margin-top:4px}
.tag{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:0 6px;margin-right:4px}.t-b{background:var(--red);color:#fff}.t-s{background:var(--navy);color:#fff}
.state{font-size:14px;font-weight:700;margin-top:6px}.s-ok{color:var(--ok)}.s-fix{color:var(--red)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.row button{flex:1 1 140px}.box{display:none;margin-top:8px}.box.open{display:block}
.gap{margin-top:16px}.gap2{margin-top:24px}.gap3{margin-top:8px}.msg.good{color:var(--ok)}.msg{min-height:1.5em;font-size:14px;color:var(--red)}.more{width:100%;margin-top:8px}
</style></head><body><main>
<h1>記事の確認（編集部用）</h1>
<p class="muted">読んだ記事にだけ「確認済み」を付けてください。毎日やる必要はありません。速報や反響の大きい記事から、読める分だけで大丈夫です。</p>
${authed ? listHtml() : loginHtml(mailReady)}
</main><script nonce="${nonce}">${authed ? LIST_JS : LOGIN_JS}</script></body></html>`;
}

function loginHtml(mailReady: boolean): string {
  if (!mailReady) return `<p>この画面はまだ準備中です（送信先のメールアドレスが未設定）。</p>`;
  return `<div class="card"><p><b>1.</b> 下のボタンを押すと、編集部のメールに6桁の確認コードが届きます。</p>
<button class="primary" id="send">確認コードをメールで受け取る</button>
<p class="gap"><b>2.</b> 届いたコードを入れて「ログイン」を押します（30日間は入れ直し不要）。</p>
<input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6桁の数字">
<div class="row"><button class="primary" id="login">ログイン</button></div><p class="msg" id="msg" role="status"></p></div>`;
}

function listHtml(): string {
  return `<div class="tabs" role="group" aria-label="表示する記事">
<button data-f="todo" aria-pressed="true">未確認</button><button data-f="priority" aria-pressed="false">速報・SNS</button><button data-f="ok" aria-pressed="false">確認済み</button><button data-f="fix" aria-pressed="false">直してほしい</button><button data-f="all" aria-pressed="false">すべて</button></div>
<p class="msg" id="msg" role="status"></p><div id="list"><p class="muted">読み込み中…</p></div>
<button class="more" id="more" hidden>もっと見る</button>
<p class="muted gap2">「確認済み」は、15〜30分ほどで記事ページに「編集部が確認済み」と表示されます。「直してほしい」は修正の依頼として記録されます。<br><button id="logout" class="gap3">ログアウト</button></p>`;
}

const COMMON_JS = `
const msg=document.getElementById('msg');
async function post(path,body){const r=await fetch('/review/api/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Saisoku-Review':'1'},body:JSON.stringify(body||{}),credentials:'same-origin'});let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.error||'うまくいきませんでした（'+r.status+'）');return d}
`;

const LOGIN_JS = `${COMMON_JS}
document.getElementById('send').onclick=async(e)=>{e.target.disabled=true;msg.textContent='送っています…';try{await post('code');msg.classList.add('good');msg.textContent='メールを送りました。届いた6桁のコードを入れてください。'}catch(err){msg.classList.remove('good');msg.textContent=err.message}setTimeout(()=>{e.target.disabled=false},60000)};
document.getElementById('login').onclick=async(e)=>{e.target.disabled=true;try{await post('login',{code:document.getElementById('code').value});location.reload()}catch(err){msg.classList.remove('good');msg.textContent=err.message;e.target.disabled=false}};
`;

const LIST_JS = `${COMMON_JS}
let data={articles:[],records:{}},filter='todo',shown=30;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=iso=>{const d=new Date(iso);return isNaN(d)?'':(d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')};
const state=s=>{const r=data.records[s];return r&&r.status!=='clear'?r:null};
function pick(){return data.articles.filter(a=>{const r=state(a.slug);if(filter==='todo')return !r;if(filter==='priority')return !r&&(a.breaking||a.sns);if(filter==='ok')return r&&r.status==='ok';if(filter==='fix')return r&&r.status==='fix';return true})}
function render(){const items=pick();const list=document.getElementById('list');if(!items.length){list.innerHTML='<p class="muted">この一覧に記事はありません。</p>';document.getElementById('more').hidden=true;return}
list.innerHTML=items.slice(0,shown).map(a=>{const r=state(a.slug);const st=r?(r.status==='ok'?'<div class="state s-ok">✓ 確認済み（'+fmt(r.at)+'）</div>':'<div class="state s-fix">直してほしい（'+fmt(r.at)+'）</div>'):'';
return '<div class="card" data-slug="'+esc(a.slug)+'"><div>'+(a.breaking?'<span class="tag t-b">速報</span>':'')+(a.sns?'<span class="tag t-s">SNS</span>':'')+'<a href="/articles/'+encodeURIComponent(a.slug)+'?_internal=1" target="_blank" rel="noopener">'+esc(a.title)+'</a></div><div class="meta">'+esc(a.genre)+'・掲載 '+fmt(a.generatedAt)+'</div>'+st+
'<div class="row">'+(r?'<button data-act="clear">取り消す</button>':'<button class="primary" data-act="ok">確認済みにする</button><button class="warn" data-act="openfix">直してほしい</button>')+'</div>'+
'<div class="box"><textarea rows="3" maxlength="300" placeholder="どこを直してほしいか（任意。短くてOK）"></textarea><div class="row"><button class="warn" data-act="fix">直してほしいと送る</button></div></div></div>'}).join('');
document.getElementById('more').hidden=items.length<=shown}
document.getElementById('list').onclick=async(e)=>{const b=e.target.closest('button');if(!b)return;const card=b.closest('.card');const slug=card.dataset.slug;const act=b.dataset.act;
if(act==='openfix'){card.querySelector('.box').classList.toggle('open');return}
b.disabled=true;msg.textContent='';try{const note=act==='fix'?card.querySelector('textarea').value:'';const d=await post('mark',{slug,status:act,note});data.records[slug]={status:d.status,at:d.at};render()}catch(err){msg.textContent=err.message;b.disabled=false}};
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{filter=b.dataset.f;shown=30;document.querySelectorAll('.tabs button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));render()});
document.getElementById('more').onclick=()=>{shown+=30;render()};
document.getElementById('logout').onclick=async()=>{try{await post('logout')}catch(e){}location.reload()};
fetch('/review/api/list',{credentials:'same-origin'}).then(r=>{if(r.status===401){location.reload();throw new Error('')}return r.json()}).then(d=>{data=d;render()}).catch(err=>{if(err.message)msg.textContent='読み込めませんでした。ページを開き直してください。'});
`;

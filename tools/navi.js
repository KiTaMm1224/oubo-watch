// 応募ナビ（claude.ai の Artifact）の HTML を、会話に読み込まずに扱うための道具。
// 応募ナビは Artifact ツールの read に path "index.html" をつけてファイルに保存してから使う。
//
//   node tools/navi.js list <応募ナビ.html>                … 載っている募集を「名称｜締切｜URL」で1行ずつ出す
//   node tools/navi.js find <応募ナビ.html> <ことば…>      … 名称・主催・URL にことばを含む募集だけ出す（重なりの確認用）
//   node tools/navi.js has  <応募ナビ.html> <digest.md>    … digest の新着・変更のうち、応募ナビに「ない」ものと「ある」ものを分けて出す
//   node tools/navi.js add  <応募ナビ.html> <追加分.js>    … 追加分（{ k:'c', ... } をカンマで並べたもの）を ITEMS の最後に足し、
//                                                           構文と件数を確かめてから上書きする（だめなら何も書き換えない）
const fs = require('fs');
const vm = require('vm');

const A = 'const AI_UNKNOWN', B = 'const $ = id';

function load(file) {
  const html = fs.readFileSync(file, 'utf8');
  const a = html.indexOf(A), b = html.indexOf(B);
  if (a < 0 || b < 0) { console.log('NG：応募ナビの形が変わった（目印が見つからない）'); process.exit(2); }
  const { ITEMS } = vm.runInContext(html.slice(a, b) + '\n;({ITEMS})', vm.createContext({}), { timeout: 3000 });
  return { html, ITEMS };
}

const canon = u => {
  if (!u) return '';
  try { const x = new URL(u); return (x.host.toLowerCase() + x.pathname.replace(/\/+$/, '')).replace(/^www\./, ''); }
  catch (e) { return String(u).trim(); }
};
const squash = s => String(s || '').normalize('NFKC').toLowerCase().replace(/[\s「」『』【】（）()［］\[\]・、。,.!！?？:：\-ー〜~"'’“”]/g, '');

function line(i) { return [i.n, i.d || '随時', i.u || 'URLなし'].join('｜'); }

function digestRows(md) {
  // 「- [サイト] 名前｜日付：…｜URL」と「- 日付｜[サイト] 名前｜変化｜URL」の行を拾う
  const rows = [];
  let section = '';
  for (const raw of md.split('\n')) {
    if (raw.startsWith('## ')) section = raw.includes('新しく出た') ? 'new' : raw.includes('日付が変わった') ? 'changed' : '';
    if (!section || !raw.startsWith('- ')) continue;
    const parts = raw.slice(2).split('｜');
    const url = parts[parts.length - 1].trim();
    if (!/^https?:\/\//.test(url)) continue;
    let title = section === 'new' ? parts[0] : (parts[1] || '');
    title = title.replace(/^\[[^\]]*\]\s*/, '').trim();
    rows.push({ section, title, url, text: raw });
  }
  return rows;
}

function match(items, url, title) {
  const cu = canon(url), st = squash(title);
  return items.find(i => (i.u && canon(i.u) === cu) ||
    (st.length >= 6 && squash(i.n).length >= 6 && (st.includes(squash(i.n)) || squash(i.n).includes(st))));
}

const [cmd, file, arg, ...rest] = process.argv.slice(2);
if (!cmd || !file) { console.log('使い方：node tools/navi.js list|find|has|add <応募ナビ.html> [...]'); process.exit(1); }

if (cmd === 'list') {
  const { ITEMS } = load(file);
  console.log(`応募ナビ：${ITEMS.length}件`);
  ITEMS.forEach(i => console.log(line(i)));
} else if (cmd === 'find') {
  const { ITEMS } = load(file);
  const words = [arg, ...rest].filter(Boolean).map(squash);
  const hit = ITEMS.filter(i => words.some(w => squash(i.n + i.o + i.u).includes(w)));
  console.log(`見つかった：${hit.length}件`);
  hit.forEach(i => console.log(line(i)));
} else if (cmd === 'has') {
  const { ITEMS } = load(file);
  const rows = digestRows(fs.readFileSync(arg, 'utf8'));
  const missing = [], present = [];
  for (const r of rows) {
    const m = match(ITEMS, r.url, r.title);
    (m ? present : missing).push({ r, m });
  }
  console.log(`digest の新着・変更 ${rows.length}件 → 応募ナビにない ${missing.length}件／ある ${present.length}件`);
  console.log('\n【応募ナビにない（確かめる）】');
  missing.forEach(({ r }) => console.log((r.section === 'changed' ? '（日付が変わった）' : '') + r.text.slice(2)));
  console.log('\n【応募ナビにある（新着なら飛ばしてよい。日付が変わったものは締切を確かめて直す）】');
  present.forEach(({ r, m }) => console.log((r.section === 'changed' ? '（日付が変わった）' : '') + r.title.slice(0, 40) + ' → 応募ナビ：' + line(m)));
} else if (cmd === 'add') {
  const { html, ITEMS } = load(file);
  const add = fs.readFileSync(arg, 'utf8').trim().replace(/,\s*$/, '');
  let parsed;
  try {
    parsed = vm.runInContext('[' + add + ']', vm.createContext({}), { timeout: 3000 });
  } catch (e) { console.log('NG：追加分の書き方にまちがいがある：' + e.message); process.exit(3); }
  const bad = parsed.filter(i => !i || !i.n || !i.k);
  if (bad.length) { console.log('NG：名称(n)か種類(k)がない項目がある'); process.exit(3); }
  const dup = parsed.filter(i => match(ITEMS, i.u, i.n));
  if (dup.length) { console.log('NG：すでに応募ナビにある募集が入っている：' + dup.map(i => i.n).join('、')); process.exit(3); }
  const start = html.indexOf('const ITEMS = [');
  const end = html.indexOf('\n  ];', start);
  if (start < 0 || end < 0) { console.log('NG：ITEMS の終わりが見つからない'); process.exit(2); }
  let head = html.slice(0, end).replace(/\s+$/, '');
  if (!head.endsWith(',') && !head.endsWith('[')) head += ',';
  const out = head + '\n' + add.split('\n').map(l => (l.startsWith('    ') ? l : '    ' + l)).join('\n') + ',' + html.slice(end);
  // 書き換えたあとも、元と同じ読み方で読めて、件数がちょうど増えていることを確かめる
  const a = out.indexOf(A), b = out.indexOf(B);
  let after;
  try {
    after = vm.runInContext(out.slice(a, b) + '\n;({ITEMS})', vm.createContext({}), { timeout: 3000 }).ITEMS;
  } catch (e) { console.log('NG：書き換えると script が壊れる：' + e.message); process.exit(4); }
  if (after.length !== ITEMS.length + parsed.length) { console.log(`NG：件数が合わない（${ITEMS.length}+${parsed.length}≠${after.length}）`); process.exit(4); }
  const scripts = [...out.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  for (const s of scripts) {
    try { new vm.Script(s); } catch (e) { console.log('NG：ページの script の構文エラー：' + e.message); process.exit(4); }
  }
  fs.writeFileSync(file, out);
  console.log(`OK：${parsed.length}件を足した（${ITEMS.length} → ${after.length}件）。このファイルを publish する`);
} else {
  console.log('使い方：node tools/navi.js list|find|has|add <応募ナビ.html> [...]');
  process.exit(1);
}

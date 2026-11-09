// 文字数などのチェック。使い方：node ck.js <ファイル> [上限の字数] [下限の字数]
const t=require('fs').readFileSync(process.argv[2],'utf8'),mx=+process.argv[3]||0,mn=+process.argv[4]||0;
const all=[...t].length,body=[...t.replace(/\s/g,'')].length;
console.log('文字数：'+all+'（空白・改行を除くと '+body+'）'+(mx?'｜上限 '+mx+' の '+Math.round(body/mx*100)+'%'+(body>mx?'　★上限を超えている':body<mx*0.9?'　★9割に届いていない':''):'')+(mn&&body<mn?'｜★下限 '+mn+' に足りない':''));
const W=['革新的','画期的','シームレス','次世代','を実現','多様なニーズ','が重要です','重要である','多くの','さまざまな','様々な'];
t.split('\n').forEach((l,n)=>W.forEach(w=>{if(l.includes(w))console.log('決まり文句「'+w+'」：'+(n+1)+'行目：'+l.trim().slice(0,40));}));
const B=t.match(/【[^】]*】/g)||[];console.log('【 】の空欄：'+B.length+'か所'+(B.length?'\n'+B.map(s=>'・'+s.slice(0,50)).join('\n'):''));

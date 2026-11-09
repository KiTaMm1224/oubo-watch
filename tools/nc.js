// 新着さがし。使い方：node tools/nc.js <保存した応募ナビのHTML> '<判定のプロフィール（JSON）>'
const fs=require('fs'),vm=require('vm'),H=s=>require('crypto').createHash('sha1').update(String(s).trim()).digest('hex').slice(0,8);
const html=fs.readFileSync(process.argv[2],'utf8'),a=html.indexOf('const AI_UNKNOWN'),b=html.indexOf('const $ = id');
if(a<0||b<0){console.log('NG：応募ナビの形が変わった');process.exit(2);}
const {ITEMS}=vm.runInContext(html.slice(a,b)+'\n;({ITEMS})',vm.createContext({}),{timeout:2000});
const N='5b98d119 d6c5c6f9 12a7ecb7 9626523a e4747ecc c21f0b49 cee61eee dcc33899 2e179502 e40ee178 ba54773b 59e42868 e9eba774 37348b7b 378be33e 127665b5 db2a53c4 5d71792d 69944108 43d0789e 04f713a7 e2e8f18d 0a25b964 9382c866 cc2bdb1e 55e07824 e72f945b 9d95014c f066d711 253f882c 2532c310 df6a8b3d 0d467382 17f503b8 fc6aa86c 4980dd1c 8a744158 8fa4f1e2 050f9418 00aec1b0 814cfd02 9bd8d9ad cb4e1e3c ee74da3e c2bcf1fd 50d3a401 916fc92d 697ba86f c5fc2801 21f9989b 7ea664e0 fa737828 467cc34a d286a55f eed95fb5';
const U='61b56087 6ab4f3ee cfd6ffea 402476c2 26d0fe4a 54745d6a def35523 785a064e 0c9c4a49 734daf10 baf04af7 b72f3278 ad248f1e 3b83c5f3 217db323 5b392d26 0e208434 56b129f9 173c836e 94e1b162 76e09870 37f4b566 b0d5f627 85136f5d e2c6a4eb 5974686b 6e1cabb0 855abbdb f944c1de bf0a5e7a 45301c36 fb65b924 6b747ff6 d35390fc 1cf19b20 a401664b 2609b5c5 04bc4436 7d425274 8a3796f0 4d033e1b 7f6f6d87 953df3d5 f6e87083 6eaea797 ea53113b d6caaa0b f7770383 bf46258e 2045ab74 62cd7219 a1c8ba62 60ee4437 5e7fbb65 81025ebf';
let p=null;try{p=JSON.parse(process.argv[3]||'null');}catch(e){}
if(!p){console.log('NG：判定のプロフィールを2つ目の引数に JSON で渡す');process.exit(1);}
const j=new Date(Date.now()+324e5),day=d=>d.toISOString().slice(0,10),today=day(j),tmr=day(new Date(j.getTime()+864e5));
const r=ITEMS.filter(i=>['c','o','l'].includes(i.k)&&i.d&&!i.approx&&i.d>=today&&!(i.ng&&i.ng(p))&&!N.includes(H(i.n)))
 .map(i=>({...i,w:(i.soft&&i.soft(p))||(i.check&&i.check(p))||''})).sort((x,y)=>x.d.localeCompare(y.d)||(!!x.w-!!y.w));
const m=ITEMS.filter(i=>N.includes(H(i.n))).length;
console.log('今日 '+today+'／新しい候補 '+r.length+'件／対象外の照合 '+m+'/55件'+(m<55?'　★そろわない（この回は「自動の対象外」の表も読んで除く）':''));
r.forEach((i,n)=>console.log([n+1,i.d+(i.t?' '+i.t:''),i.w?'△':'○',{c:'コンテスト',o:'海外',l:'プログラム'}[i.k],i.n,i.o,i.u||'URLなし',i.w,i.ai?'AI：'+i.ai:'',i.d<=tmr?'★締切が明日以前':'',i.u&&U.includes(H(i.u))?'★対象外の募集とURLが同じ（同じ回なら除く）':''].filter(Boolean).join('｜')));
r.slice(0,4).forEach((i,n)=>console.log('\n['+(n+1)+'] '+i.n+'\n出せる理由：'+(i.r||'')+'\n応募の準備：'+(i.a||'')));

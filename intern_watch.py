#!/usr/bin/env python3
"""インターン監視（UT-Board・ゼロワンインターン）

時給の下限が2,000円以上で、コンサル・データ分析・AI系の長期インターンを集め、
前回との違い（新着・募集終了・募集再開）を data/intern_digest.md にまとめる。
書くのは data/intern_state.json と data/intern_digest.md だけ（応募ナビ用のファイルには触らない）。
"""
import datetime
import json
import os
import re
import time
import urllib.robotparser

import requests
from bs4 import BeautifulSoup

JST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
STATE = "data/intern_state.json"
DIGEST = "data/intern_digest.md"
MIN_WAGE = 2000
NEW_DAYS = 7      # この日数以内に見つけたものを「新着」とする
WAIT = 1.5        # 1回ごとに待つ秒数（サイトに負担をかけない）

# 募集の再開を見張る求人
WATCH = [
    {"key": "01:7753", "name": "トランスリー（実データ×AIコンサル）",
     "url": "https://01intern.com/job/7753.html"},
]

UT_LIST = "https://ut-board.com/recruitment/gte_2000/"
Z_LISTS = [
    ("ゼロワン コンサル業界", "https://01intern.com/job/list.html?businessTypes=4&jobTags=845"),
    ("ゼロワン IT業界の企画", "https://01intern.com/job/list.html?businessTypes=1&jobTypes=7&jobTags=845"),
]

CORE_RE = re.compile(r"コンサル|分析|データ|DX|リサーチ|提案|課題解決|業務改善|業務改革|PMO|戦略")
AI_RE = re.compile(r"AI|ＡＩ|生成|LLM|DX|SaaS|データ|IT|デジタル")
AI_TAGS = {"機械学習・AI", "データサイエンス", "IT業界"}
SKIP_OCC = re.compile(r"エンジニア|プログラミング|デザイナー|ライター|編集|経理")
SKIP_FIN = re.compile(r"M&A|Ｍ＆Ａ|ＰＥ|PEファンド|ファンド|投資|金融")

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_robots = {}


def now_jst():
    return datetime.datetime.now(JST)


def robots_ok(url):
    root = "/".join(url.split("/")[:3])
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = S.get(root + "/robots.txt", timeout=20)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[root] = rp
    return _robots[root].can_fetch("*", url)


def get(url):
    if not robots_ok(url):
        raise RuntimeError("robots.txt で禁止")
    time.sleep(WAIT)
    r = S.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    return r.text


def txt(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def wage_min(s):
    m = re.search(r"([\d,]+)\s*円", s or "")
    return int(m.group(1).replace(",", "")) if m else 0


def short(s, n=110):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def classify(title, occ, tags):
    """候補の種類を返す。対象外なら None。"""
    occ_s = " ".join(occ)
    core_occ = bool(re.search(r"コンサル|企画", occ_s))
    if SKIP_OCC.search(occ_s) and not core_occ:
        return None
    if SKIP_FIN.search(title) or "金融" in occ_s:
        return None
    core = bool(CORE_RE.search(title)) or "コンサルティング" in occ_s
    ai = bool(AI_RE.search(title)) or bool(AI_TAGS & set(tags))
    if re.search(r"事務|アシスタント", occ_s) and not core:
        return None
    if re.search(r"マーケティング", occ_s) and not core_occ and not core:
        return None
    if core and ai:
        return "本命"
    if core:
        return "コンサル系"
    if "営業" in occ_s and ai:
        return "AI・IT営業"
    if "企画" in occ_s and ai:
        return "AI企画"
    return None


# ---------- UT-Board ----------
def ut_list():
    items, pages = {}, 0
    for n in range(1, 15):
        try:
            html = get(UT_LIST + (f"?page={n}" if n > 1 else ""))
        except RuntimeError as e:
            if n > 1 and "404" in str(e):
                break       # 最後のページの次は404になる
            raise
        cards = BeautifulSoup(html, "html.parser").select("article.recruitment-card-v2")
        added = 0
        for c in cards:
            a = c.select_one("h2 a[href]")
            m = re.search(r"/recruitment/(\d+)/", a.get("href", "") if a else "")
            if not m or ("ut:" + m.group(1)) in items:
                continue
            ems = [txt(e) for e in c.select("strong.emphasis-value")]
            occ = [txt(x) for x in c.select('a[href*="/recruitment/occupation/"]')]
            loc = ""
            for row in c.select(".info-row"):
                if row.select_one('a[href*="/prefecture/"], a[href*="/full_remote/"]'):
                    loc = txt(row)
            items["ut:" + m.group(1)] = {
                "site": "UT-Board",
                "url": f"https://ut-board.com/recruitment/{m.group(1)}/",
                "company": txt(c.select_one(".company-name")),
                "title": txt(a),
                "wage": next((e for e in ems if "円" in e), ""),
                "days": next((e for e in ems if "日" in e and "円" not in e), ""),
                "occ": occ,
                "tags": [txt(x) for x in c.select("a.feature-tag")],
                "loc": loc,
                "open": not (c.select_one(".inactive-overlay") or "募集を停止" in txt(c)),
            }
            added += 1
        if not cards or not added:
            break
        pages = n
    if not items:
        raise RuntimeError("求人を1件も読めなかった（ページの形が変わった可能性）")
    return items, pages


def ut_detail(url):
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    d = {}
    for it in soup.select("div.offer-item"):
        k, v = txt(it.select_one(".left")), txt(it.select_one(".right"))
        if k:
            d[k] = v
    entry = soup.select_one("button.entry-button") or "エントリー画面に進む" in html
    stopped = not entry and "募集を停止" in html
    keep = ["給与", "シフト", "雇用形態", "必須条件"]
    return {"open": not stopped, "info": " / ".join(f"{k}: {short(d[k], 90)}" for k in keep if d.get(k))}


# ---------- ゼロワンインターン ----------
def z_list(url):
    items, pages = {}, 0
    for n in range(1, 15):
        try:
            html = get(url + (f"&page={n}" if n > 1 else ""))
        except RuntimeError as e:
            if n > 1 and "404" in str(e):
                break
            raise
        secs = BeautifulSoup(html, "html.parser").select("section.i-job-item")
        added = 0
        for s in secs:
            a = next((x for x in s.select("a[href]") if re.search(r"/job/\d+\.html", x["href"])), None)
            if not a:
                continue
            jid = re.search(r"/job/(\d+)\.html", a["href"]).group(1)
            if ("01:" + jid) in items:
                continue
            ps = [txt(p) for p in s.select("p")]
            wage = next((p for p in ps if p.startswith("時給") or "円" in p), "")
            days = next((p for p in ps if p.startswith("週")), "")
            jobtype = next((p for p in ps if p and "円" not in p and not p.startswith("週")
                            and "駅" not in p and "徒歩" not in p), "")
            items["01:" + jid] = {
                "site": "ゼロワン",
                "url": f"https://01intern.com/job/{jid}.html",
                "company": txt(s.select_one(".i-job-suptitle")),
                "title": txt(s.select_one(".i-job-title")),
                "wage": re.sub(r"^時給\s*", "", wage.split("/")[0].strip()),
                "days": days.split("/")[0].strip(),
                "occ": [jobtype],
                "tags": [txt(x) for x in s.select(".i-job-category")],
                "loc": next((p for p in ps if "駅" in p or "徒歩" in p), "")[:40],
                "open": "募集停止" not in txt(s),
            }
            added += 1
        if not secs or not added:
            break
        pages = n
    return items, pages


def z_detail(url):
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    d = {}
    for dt in soup.select("dl dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            d.setdefault(txt(dt), txt(dd))
    stopped = "現在募集を停止" in html
    keep = ["給与", "勤務日数", "その他勤務条件", "応募資格", "対象学年", "選考フロー", "雇用形態"]
    return {"open": not stopped, "info": " / ".join(f"{k}: {short(d[k], 90)}" for k in keep if d.get(k))}


def detail(it):
    return ut_detail(it["url"]) if it["site"] == "UT-Board" else z_detail(it["url"])


# ---------- まとめ ----------
def line(it):
    loc = f"｜{short(it['loc'], 30)}" if it.get("loc") else ""
    return (f"[{it['cat']}] {it['company']}｜{short(it['title'], 60)}｜{it['wage']}"
            f"｜{it['days'] or '日数不明'}{loc}｜{it['url']}")


def main():
    now = now_jst()
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - datetime.timedelta(days=NEW_DAYS)).strftime("%Y-%m-%d")
    os.makedirs("data", exist_ok=True)
    baseline = not os.path.exists(STATE)
    state = {"items": {}, "watch": {}} if baseline else json.load(open(STATE, encoding="utf-8"))
    seen_sites = set(state.get("sites_ok", [])) if "sites_ok" in state else \
        {v.get("site") for v in state.get("items", {}).values()}
    prev = state["items"]
    status, found, ok_sites = [], {}, set()

    try:
        got, pages = ut_list()
        found.update(got)
        ok_sites.add("UT-Board")
        status.append(f"UT-Board ✓（{pages}ページ・{len(got)}件）")
    except Exception as e:
        status.append(f"UT-Board ✗ {e}")
    z_ok = True
    for i, (name, url) in enumerate(Z_LISTS):
        try:
            got, pages = z_list(url)
            if i == 0 and not got:
                raise RuntimeError("求人を1件も読めなかった（ブロックかページの形が変わった可能性）")
            found.update(got)
            status.append(f"{name} ✓（{pages}ページ・{len(got)}件）")
        except Exception as e:
            z_ok = False
            status.append(f"{name} ✗ {e}")
    if z_ok:
        ok_sites.add("ゼロワン")

    # 候補を選ぶ
    cands = {}
    for k, it in found.items():
        if not it["open"] or wage_min(it["wage"]) < MIN_WAGE or re.search(r"コミッション|歩合|成果報酬", it["wage"]):
            continue
        cat = classify(it["title"], it["occ"], it["tags"])
        if cat:
            it["cat"] = cat
            cands[k] = it

    # 前回と比べる
    for k, it in cands.items():
        p = prev.get(k)
        if p and p.get("status") == "open":
            it["since"] = p.get("since", p.get("first_seen", today))
            it["first_seen"] = p.get("first_seen", today)
            it["baseline"] = p.get("baseline", False)
            it["info"] = p.get("info", "")
        else:
            it["first_seen"] = p.get("first_seen", today) if p else today
            it["since"] = today
            it["baseline"] = baseline or it["site"] not in seen_sites
            it["reopened"] = bool(p)
            it["info"] = p.get("info", "") if p else ""
        it["status"] = "open"
        it["last_seen"] = today

    ended = []
    for k, p in prev.items():
        if k in cands or p.get("status") != "open":
            continue
        if p.get("site") not in ok_sites:
            cands[k] = p        # 取れなかったサイトの分は前回のまま残す
            continue
        ended.append((k, dict(p, status="ended", ended=today)))
    old_ended = {k: p for k, p in prev.items() if p.get("status") == "ended" and k not in cands}

    # 新着の詳細ページを読む（1件につき1回だけ）
    news = [it for it in cands.values()
            if it.get("since", "") >= cutoff and not it.get("baseline") and it.get("status") == "open"]
    for it in news + [c for c in cands.values() if not c.get("info")][:25]:
        if it.get("info"):
            continue
        try:
            d = detail(it)
            it["info"] = d["info"] or "（詳細の項目が読めず）"
            if not d["open"]:
                it["note"] = "詳細ページでは募集停止"
        except Exception as e:
            it["info"] = f"（詳細を読めず：{e}）"

    # 見張りの求人
    watch_lines = []
    for w in WATCH:
        before = state.get("watch", {}).get(w["key"], "不明")
        try:
            d = z_detail(w["url"]) if "01intern" in w["url"] else ut_detail(w["url"])
            st = "募集中" if d["open"] else "停止中"
        except Exception as e:
            st = f"確認できず（{e}）"
        state.setdefault("watch", {})[w["key"]] = st if not st.startswith("確認") else before
        mark = "🔔 募集再開！ " if st == "募集中" and before != "募集中" else ""
        watch_lines.append(f"- {mark}{w['name']}：{st}（前回：{before}）｜{w['url']}")

    # 保存
    new_state_items = {k: v for k, v in cands.items()}
    for k, p in ended:
        new_state_items[k] = p
    for k, p in old_ended.items():
        if p.get("ended", "") >= (now - datetime.timedelta(days=60)).strftime("%Y-%m-%d"):
            new_state_items.setdefault(k, p)
    state["items"] = new_state_items
    state["updated"] = now.strftime("%Y-%m-%d %H:%M")
    state["sites_ok"] = sorted(seen_sites | ok_sites)
    first_sites = sorted(ok_sites - seen_sites) if not baseline else []
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    # まとめを書く
    order = {"本命": 0, "コンサル系": 1, "AI企画": 2, "AI・IT営業": 3}
    key = lambda it: (order.get(it.get("cat"), 9), -wage_min(it.get("wage")))
    out = ["# インターン監視（UT-Board・ゼロワンインターン）", "",
           f"日付: {now.strftime('%Y-%m-%d %H:%M')}（日本時間）",
           "取得: " + "／".join(status),
           f"条件: 時給の下限{MIN_WAGE:,}円以上・募集中・コンサル／データ／AI系（エンジニア・デザイナー・M&A・金融は除く）。"
           "種類：本命＝コンサル・分析×AI／コンサル系／AI企画／AI・IT営業", ""]
    if baseline:
        out += ["※ 初回の基準づくり：今回の候補は新着に数えない（次回から差分を出す）", ""]
    elif first_sites:
        out += [f"※ {'・'.join(first_sites)} は今回はじめて取得できたので、その候補は新着に数えない（次回から差分を出す）", ""]
    out += ["## 見張り"] + watch_lines + [""]
    news = sorted([it for it in cands.values() if it.get("since", "") >= cutoff
                   and not it.get("baseline") and it.get("status") == "open"], key=key)
    out.append(f"## 新着・再開（{NEW_DAYS}日以内）")
    if news:
        for it in news:
            tag = "（再開）" if it.get("reopened") else ""
            out.append(f"- {tag}{line(it)}")
            out.append(f"  - {it.get('info', '')}" + (f"／⚠ {it['note']}" if it.get("note") else ""))
    else:
        out.append("- なし")
    out += ["", f"## 募集終了・停止（{NEW_DAYS}日以内）"]
    gone = [p for p in [e[1] for e in ended] + list(old_ended.values()) if p.get("ended", "") >= cutoff and p.get("cat")]
    out += [f"- {p['company']}｜{short(p['title'], 60)}｜{p['url']}" for p in gone] or ["- なし"]
    cur = sorted([it for it in cands.values() if it.get("status") == "open"], key=key)
    out += ["", f"## いま募集中の候補（{len(cur)}件）"]
    out += [f"- {line(it)}（初確認 {it.get('first_seen', '')[5:]}）" for it in cur] or ["- なし"]
    with open(DIGEST, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()

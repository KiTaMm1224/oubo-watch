#!/usr/bin/env python3
"""応募ウォッチ

決まった募集サイトの一覧を1日1回だけ見て、前回から「新しく出た募集」と
「締切などの日付が変わった募集」を data/digest.md に短くまとめる。
Claude（応募チャンスの自動タスク）はこのリストを読むだけで済むので、
毎回サイトを最後まで見て回るよりトークンが少なくて済む。

- 扱うのは公開されている募集一覧だけ。個人情報は入れない。
- robots.txt を守り、同じサイトには DELAY 秒以上あけて取りに行く。
- 取れなかったサイトは「取得できなかった」と書き、そのサイトは
  Claude が今までどおり自分で見て回る（見落としを増やさないため）。
"""
import datetime as dt
import hashlib
import json
import pathlib
import re
import time
import traceback
import urllib.parse
import urllib.robotparser

import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCES_PATH = ROOT / "sources.json"
STATE_PATH = DATA / "state.json"
DIGEST_PATH = DATA / "digest.md"
LOG_PATH = DATA / "log.txt"

UA = "oubo-watch/1.0 (personal contest-list checker; runs once a day)"
JST = dt.timezone(dt.timedelta(hours=9))
WINDOW_DAYS = 7      # digest に載せる日数（Claude の実行は4日に1回なので、それより長くする）
FORGET_DAYS = 45     # この日数見えなかった募集は記録から消す
DELAY = 2.0          # 同じサイトへの間隔（秒）
TIMEOUT = 25
MAX_CTX = 400        # 募集1件のまわりの文字として扱う長さの上限
MAX_TITLE = 160
MAX_LIST = 80        # 1サイトで「新しく出た」がこれを超えたら、Claude に見て回ってもらう
SHRINK_LIMIT = 0.5   # 件数が前回の半分未満になったら、取り損ねとみなす
DETAIL_BUDGET = 40   # 新しく出た募集の詳細ページを、1回にこの件数まで読んで要項を抜き出す
FACT_GROUPS = (      # 詳細ページから抜き出す項目（Claude が詳細を開く前の手がかり）
    ("対象", re.compile(r"応募資格|参加資格|募集対象|応募対象|対象者|応募条件|参加条件|対象")),
    ("締切", re.compile(r"締切|〆切|締め切り|応募期間|募集期間|受付期間|エントリー期間|応募期限")),
    ("費用", re.compile(r"参加費|応募料|受講料|参加料|自己負担|費用")),
    ("人数", re.compile(r"\d+\s*(?:名|人)以内|チーム|グループ")),
    ("AI", re.compile(r"生成\s*AI|生成ＡＩ|ChatGPT|AIの(?:使用|利用)")),
    ("地域", re.compile(r"在住|在学|在勤")),
)

GENERIC = re.compile(r"(詳しく|詳細|こちら|続きを読む|もっと見る|一覧|read more|more|›|→|»)", re.I)
VOLATILE = [re.compile(p) for p in (
    r"あと\s*\d+\s*日", r"残り\s*\d+\s*日", r"締切間近", r"本日締切", r"明日締切",
    r"\d+\s*日前", r"\bNEW!?\b", r"[\[［(（]\s*PR\s*[\]］)）]",
    r"(詳細を見る|詳しく見る|続きを読む)\s*[→›»]?",
)]
DATE_RX = (
    re.compile(r"(20\d{2})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})"),
    re.compile(r"(20\d{2})(\d{2})/(\d{1,2})(?!\d)"),   # 例：Sterfield の「202612/15」
)
BLOCKS = {"li", "article", "tr", "dd", "dt", "div", "section", "td", "p", "figure"}
STOP_AT = {"body", "html", "main", "ul", "ol", "table", "tbody"}

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_robots = {}
_last_hit = {}
_log = []


def log(msg):
    stamp = dt.datetime.now(JST).strftime("%H:%M:%S")
    _log.append(f"{stamp} {msg}")
    print(msg, flush=True)


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def clean(s):
    s = norm(s)
    for rx in VOLATILE:
        s = rx.sub(" ", s)
    return norm(s)


def dates_in(s):
    found = set()
    for rx in DATE_RX:
        for y, m, d in rx.findall(s or ""):
            try:
                found.add(dt.date(int(y), int(m), int(d)).isoformat())
            except ValueError:
                pass
    return found


def canon(url):
    p = urllib.parse.urlsplit(url)
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}"


def robots_ok(url):
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt", timeout=TIMEOUT)
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp.allow_all = True
        rp.modified()
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def fetch(url):
    """ページの中身（bytes）を返す。ページがないときは None。取れないときは例外。"""
    if not robots_ok(url):
        raise PermissionError("robots.txt で取得が止められている")
    host = urllib.parse.urlsplit(url).netloc
    wait = DELAY - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    err = "取得できない"
    for attempt in range(2):
        try:
            r = session.get(url, timeout=TIMEOUT)
            _last_hit[host] = time.time()
            if r.status_code == 200:
                return r.content
            if r.status_code in (404, 410):
                return None
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(5)
    raise RuntimeError(err)


def is_generic(text):
    return (not text) or (len(text) <= 25 and GENERIC.search(text) is not None)


def context_of(a):
    best = a.get_text(" ")
    el = a
    for _ in range(6):
        el = el.parent
        if el is None or el.name in STOP_AT:
            break
        if el.name in BLOCKS:
            t = el.get_text(" ")
            if len(norm(t)) > MAX_CTX:
                break
            best = t
    return norm(best)


def merge(into, got):
    for url, rec in got.items():
        cur = into.setdefault(url, {"title": "", "dates": set()})
        t = rec["title"]
        if t and (not cur["title"] or (len(t) > len(cur["title"]) and len(t) <= MAX_TITLE)):
            cur["title"] = t[:MAX_TITLE]
        cur["dates"] |= rec["dates"]


def extract(content, page_url, src):
    soup = BeautifulSoup(content, "html.parser")
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    detail = re.compile(src["detail"])
    exclude = re.compile(src["exclude"]) if src.get("exclude") else None
    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = canon(urllib.parse.urljoin(page_url, href))
        if not detail.match(url) or (exclude and exclude.search(url)):
            continue
        text = clean(a.get_text(" "))
        ctx = context_of(a)
        head = ""
        if is_generic(text) or src.get("use_heading"):
            h = a.find_previous(["h1", "h2", "h3", "h4"])
            head = clean(h.get_text(" ")) if h else ""
        if not is_generic(text):
            title = text
        else:
            title = head or clean(ctx)
        merge(found, {url: {"title": title[:MAX_TITLE] if len(title) > MAX_TITLE else title,
                            "dates": dates_in(text) | dates_in(ctx) | dates_in(head)}})
    return found


def page_snapshot(content, page_url):
    soup = BeautifulSoup(content, "html.parser")
    for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "form", "aside"]):
        bad.decompose()
    main = soup.find("main") or soup.find(id=re.compile("content|main", re.I)) or soup.body or soup
    text = clean(main.get_text(" "))
    host = urllib.parse.urlsplit(page_url).netloc.lower()
    me = canon(page_url)
    links = {}
    for a in main.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        u = canon(urllib.parse.urljoin(page_url, href))
        if urllib.parse.urlsplit(u).netloc.lower() == host and u != me:
            links.setdefault(u, clean(a.get_text(" "))[:100])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], links


def collect_list(src):
    items = {}
    for u in src["urls"]:
        c = fetch(u)
        if c is None:
            raise RuntimeError(f"一覧のページがない（{u}）")
        merge(items, extract(c, u, src))
    if src.get("pages"):
        for n in range(2, int(src.get("max_pages", 10)) + 1):
            u = src["pages"].format(n=n)
            c = fetch(u)
            if c is None:
                break
            got = extract(c, u, src)
            fresh = set(got) - set(items)
            merge(items, got)
            if not fresh:
                break
    return items


def days_ago(day, today):
    try:
        return (dt.date.fromisoformat(today) - dt.date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return 9999


def run_list(src, prev, today):
    items = collect_list(src)
    if not items:
        raise RuntimeError("募集が0件（サイトの形が変わったかもしれない）")
    prev_count = (prev or {}).get("count", 0)
    if prev_count and len(items) < prev_count * SHRINK_LIMIT:
        raise RuntimeError(f"件数が前回の半分未満（{prev_count}件 → {len(items)}件）。取り損ねの可能性")
    baseline = prev is None
    known = {} if baseline else dict(prev.get("items", {}))
    new_today = 0
    for url, rec in items.items():
        dates = sorted(rec["dates"])
        old = known.get(url)
        if old is None:
            known[url] = {"title": rec["title"], "dates": dates, "first_seen": today,
                          "last_seen": today, "baseline": baseline}
            if not baseline:
                new_today += 1
        else:
            old["last_seen"] = today
            if rec["title"]:
                old["title"] = rec["title"]
            old_dates = set(old.get("dates", []))
            added = set(dates) - old_dates
            if old_dates and added:
                old["changed_on"] = today
                old["change"] = f"{'・'.join(sorted(old_dates)[-3:])} → {'・'.join(sorted(added))}"
            old["dates"] = sorted(old_dates | set(dates))[-6:]
    for url in [u for u, r in known.items() if days_ago(r.get("last_seen"), today) > FORGET_DAYS]:
        del known[url]
    if baseline:
        baseline_on = today
    else:  # 基準を作った日（同じ日のうちは新着の判定をしない）
        baseline_on = prev.get("baseline_on") or min(
            [r["first_seen"] for r in known.values() if r.get("baseline") and r.get("first_seen")] or [today])
    return {"type": "list", "count": len(items), "items": known, "last_ok": today,
            "baseline_on": baseline_on}, new_today


def run_page(src, prev, today):
    c = fetch(src["urls"][0])
    if c is None:
        raise RuntimeError("ページがない")
    h, links = page_snapshot(c, src["urls"][0])
    if prev is None:
        return {"type": "page", "hash": h, "links": {u: {"t": t, "first_seen": today} for u, t in links.items()},
                "last_ok": today, "baseline_on": today}, 0
    st = dict(prev)
    old_links = dict(st.get("links", {}))
    added = 0
    for u, t in links.items():
        if u not in old_links:
            old_links[u] = {"t": t, "first_seen": today}
            added += 1
    if h != st.get("hash"):
        st["changed_on"] = today
    st.update({"hash": h, "links": old_links, "last_ok": today})
    return st, added


def write_digest(sources, state, status, now):
    today = now.date().isoformat()
    md = []
    md.append("# 応募ウォッチ（GitHub が作る新着リスト）")
    md.append("")
    md.append(f"- 作った日時：{now.strftime('%Y-%m-%d %H:%M')}（日本時間）")
    md.append(f"- 日付：{today}")
    md.append(f"- 対象の期間：直近{WINDOW_DAYS}日（Claude の実行が4日に1回でも漏れないように）")
    md.append("- 使い方：「✓ 取得できた」サイトは、下の「新しく出た募集」「締切などの日付が変わった募集」だけ確かめれば足りる。"
              "「✗ 取得できなかった」「★ 基準づくり」「▲ 多すぎる」のサイトは、今までどおり自分で見て回る。")
    md.append("")
    md.append("## サイトごとの状態")
    for src in sources:
        s = status[src["id"]]
        md.append(f"- {s['mark']} {src['name']}：{s['text']}")
    md.append("")

    new_rows, changed_rows, facts = [], [], {}
    for src in sources:
        st = state["sources"].get(src["id"])
        if not st or st.get("type") != "list" or not status[src["id"]]["ok"]:
            continue
        for url, r in st["items"].items():
            if not r.get("baseline") and days_ago(r.get("first_seen"), today) <= WINDOW_DAYS:
                new_rows.append((r["first_seen"], src["name"], r["title"], r.get("dates", []), url))
                if r.get("facts"):
                    facts[url] = r["facts"]
            if r.get("changed_on") and days_ago(r["changed_on"], today) <= WINDOW_DAYS:
                changed_rows.append((r["changed_on"], src["name"], r["title"], r.get("change", ""), url))

    def dedup(rows):  # 同じ募集が複数のサイト・一覧に出ていても1回だけ載せる
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: (r[0], r[1]), reverse=True):
            if r[-1] not in seen:
                seen.add(r[-1])
                out.append(r)
        return out

    new_rows, changed_rows = dedup(new_rows), dedup(changed_rows)
    md.append(f"## 新しく出た募集（直近{WINDOW_DAYS}日、{len(new_rows)}件）")
    if not new_rows:
        md.append("- なし")
    for day in sorted({r[0] for r in new_rows}, reverse=True):
        md.append(f"### {day}")
        for _, name, title, dates, url in sorted([r for r in new_rows if r[0] == day], key=lambda r: (r[1], r[2])):
            d = f"｜日付：{'・'.join(dates[-3:])}" if dates else ""
            md.append(f"- [{name}] {title or '（名前が取れず）'}{d}｜{url}")
            if facts.get(url):
                md.append(f"  - 要項の抜き出し：{facts[url]}")
    md.append("")
    md.append(f"## 締切などの日付が変わった募集（直近{WINDOW_DAYS}日、{len(changed_rows)}件）")
    if not changed_rows:
        md.append("- なし")
    for day, name, title, change, url in sorted(changed_rows, reverse=True):
        md.append(f"- {day}｜[{name}] {title}｜{change}｜{url}")
    md.append("")
    md.append(f"## 公式ページの変化（直近{WINDOW_DAYS}日）")
    for src in sources:
        st = state["sources"].get(src["id"])
        if src.get("type") != "page":
            continue
        if not st or not status[src["id"]]["ok"]:
            md.append(f"- {src['name']}：確認できなかった → 自分で見る｜{src['urls'][0]}")
            continue
        if st.get("baseline_on") == today:
            md.append(f"- {src['name']}：基準づくり（今日が初回）→ 自分で見る｜{src['urls'][0]}")
            continue
        changed = st.get("changed_on") and days_ago(st["changed_on"], today) <= WINDOW_DAYS
        fresh = [(u, v["t"]) for u, v in st.get("links", {}).items()
                 if v.get("first_seen") != st.get("baseline_on") and days_ago(v.get("first_seen"), today) <= WINDOW_DAYS]
        line = f"- {src['name']}：" + (f"{st['changed_on']} に中身が変わった → 確かめる" if changed else "変わっていない")
        md.append(line + f"｜{src['urls'][0]}")
        for u, t in fresh[:15]:
            md.append(f"  - 新しいリンク：{t or '（文字なし）'}｜{u}")
    md.append("")
    DIGEST_PATH.write_text("\n".join(md) + "\n", encoding="utf-8")


def facts_of(content):
    """詳細ページから「対象・締切・費用・人数・AI・地域」の近くの文を短く抜き出す。"""
    soup = BeautifulSoup(content, "html.parser")
    for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "form", "aside"]):
        bad.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [norm(x) for x in main.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    out, used = [], set()
    for label, rx in FACT_GROUPS:
        for i, line in enumerate(lines):
            m = rx.search(line)
            if not m:
                continue
            if i not in used:   # 同じ行はくり返さない
                used.add(i)
                text = line
                if len(text) < 25 and i + 1 < len(lines):   # 表の見出しだけの行なら、次の行（中身）をつなげる
                    used.add(i + 1)
                    text += " " + lines[i + 1]
                start = max(0, m.start() - 15)
                out.append(f"{label}「{text[start:start + 90]}」")
            break
    return "／".join(out)[:600]


def add_facts(sources, state, status, today):
    budget = DETAIL_BUDGET
    for src in sources:
        if src.get("type") == "page" or not status.get(src["id"], {}).get("ok"):
            continue
        items = state["sources"][src["id"]]["items"]
        for url, r in items.items():
            if budget <= 0:
                return
            if r.get("baseline") or r.get("first_seen") != today or r.get("facts_on"):
                continue
            budget -= 1
            r["facts_on"] = today
            try:
                c = fetch(url)
                r["facts"] = facts_of(c) if c else ""
            except Exception as e:  # 読めなければ抜き出しなし（Claude が今までどおり詳細を開く）
                r["facts"] = ""
                log(f"facts NG {url}: {e}")


def main(now=None):
    now = now or dt.datetime.now(JST)
    today = now.date().isoformat()
    DATA.mkdir(exist_ok=True)
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("sources", {})
    status = {}
    for src in sources:
        sid = src["id"]
        prev = state["sources"].get(sid)
        t0 = time.time()
        try:
            if src.get("type") == "page":
                st, added = run_page(src, prev, today)
                state["sources"][sid] = st
                if prev is None or st.get("baseline_on") == today:
                    status[sid] = {"ok": True, "mark": "★", "text": "基準づくり（今日が初回。変化の判定は次回から）"}
                else:
                    status[sid] = {"ok": True, "mark": "✓", "text": "取得できた"}
            else:
                st, new_today = run_list(src, prev, today)
                window_new = sum(1 for r in st["items"].values()
                                 if not r.get("baseline") and days_ago(r.get("first_seen"), today) <= WINDOW_DAYS)
                state["sources"][sid] = st
                if prev is None or st.get("baseline_on") == today:
                    status[sid] = {"ok": False, "mark": "★",
                                   "text": f"基準づくり（今日が初回で{st['count']}件を記録。新着の判定は明日から）"}
                elif window_new > MAX_LIST:
                    status[sid] = {"ok": False, "mark": "▲",
                                   "text": f"新着が多すぎる（{window_new}件）→ 自分で見て回る"}
                else:
                    status[sid] = {"ok": True, "mark": "✓",
                                   "text": f"取得できた（一覧{st['count']}件、今日の新着{new_today}件）"}
            log(f"OK {sid} {time.time() - t0:.1f}s")
        except Exception as e:  # 取れなかったサイトは前回の記録を残し、Claude に見て回ってもらう
            status[sid] = {"ok": False, "mark": "✗", "text": f"取得できなかった（{e}）→ 自分で見て回る"}
            log(f"NG {sid}: {e}")
            log(traceback.format_exc(limit=2))
    add_facts(sources, state, status, today)
    state["last_run"] = now.isoformat(timespec="minutes")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    write_digest(sources, state, status, now)
    LOG_PATH.write_text("\n".join(_log) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

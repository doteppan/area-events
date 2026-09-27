#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""店舗周辺イベント情報ページの生成(標準ライブラリのみ。GitHub Actionsで毎朝実行)

sources.json の各店のRSS(みんなの経済新聞)を取得し、記事本文から開催日(M月D日)を抜き出して
「これからのイベント」を日付順に並べる。生成物: index.html / data.json
"""
import html, json, os, re, sys, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf-8'))
TODAY = date.today()
UPCOMING_DAYS = 90      # 何日先まで「これから」に出すか
RECENT_ARTICLES = 12    # 最新記事の表示件数
UA = 'Mozilla/5.0 (compatible; doteppan-area-events/1.0)'

DATE_RE = re.compile(r'(\d{1,2})月(\d{1,2})日')
RANGE_RE = re.compile(r'(\d{1,2})月(\d{1,2})日\s*[〜~～ー－-]\s*(?:(\d{1,2})月)?(\d{1,2})日')
PAIR_RE = re.compile(r'(\d{1,2})月(\d{1,2})日[・、](\d{1,2})日')   # 「9月26日・27日」形式


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    for _ in range(3):
        try:
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            err = e
            time.sleep(3)
    print(f'取得失敗 {url}: {err}', file=sys.stderr)
    return None


def guess_year(month, day, pub):
    """記事の公開日を基準に、M月D日の年を推定(公開日より2か月以上前なら翌年)"""
    y = pub.year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if (d - pub.date()).days < -60:
        try:
            d = date(y + 1, month, day)
        except ValueError:
            return None
    return d


def extract_dates(text, pub):
    """本文から開催日を抽出 → [(開始日, 終了日)]。範囲表記(10月10日〜12日)にも対応"""
    out = []
    used = set()
    for m in RANGE_RE.finditer(text):
        m1, d1, m2, d2 = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        s = guess_year(m1, d1, pub)
        e = guess_year(int(m2) if m2 else m1, d2, pub)
        if s and e and e >= s:
            out.append((s, e))
            used.add((m1, d1))
            used.add((int(m2) if m2 else m1, d2))
    for m in PAIR_RE.finditer(text):
        mo, d1, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if (mo, d1) in used:
            continue
        a, b = guess_year(mo, d1, pub), guess_year(mo, d2, pub)
        if a and b:
            out.append((a, b) if b >= a else (a, a))
            used.add((mo, d1))
            used.add((mo, d2))
    for m in DATE_RE.finditer(text):
        mo, da = int(m.group(1)), int(m.group(2))
        if (mo, da) in used:
            continue
        d = guess_year(mo, da, pub)
        if d:
            out.append((d, d))
    return out


def parse_feed(feed):
    raw = fetch(feed['rss'])
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f'RSS解析失敗 {feed["rss"]}: {e}', file=sys.stderr)
        return []
    items = []
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        link = (it.findtext('link') or '').strip()
        desc = re.sub(r'<[^>]+>', '', it.findtext('description') or '').replace('#' + feed['name'], '').strip()
        desc = re.sub(r'\s*#\S+$', '', desc)
        pd = it.findtext('pubDate')
        try:
            pub = parsedate_to_datetime(pd) if pd else datetime.now()
        except Exception:
            pub = datetime.now()
        enc = it.find('enclosure')
        img = enc.get('url') if enc is not None else ''
        text = title + ' ' + desc
        dates = extract_dates(text, pub)
        kw = [k for k in SRC['event_keywords'] if k in text]
        items.append({'title': title, 'link': link, 'desc': desc, 'pub': pub.strftime('%Y-%m-%d'),
                      'img': img, 'source': feed['name'],
                      'dates': [(s.isoformat(), e.isoformat()) for s, e in dates], 'keywords': kw})
    return items


def build_store(store):
    items = []
    for f in store['feeds']:
        items += parse_feed(f)
    # 最新記事(公開日降順・重複リンク除去)
    seen = set()
    recent = []
    for it in sorted(items, key=lambda x: x['pub'], reverse=True):
        if it['link'] in seen:
            continue
        seen.add(it['link'])
        recent.append(it)
    # これからのイベント: 終了日が今日以降〜90日以内の日付を持つ記事(イベント語を含むもの優先)
    upcoming = []
    limit = TODAY + timedelta(days=UPCOMING_DAYS)
    for it in recent:
        for s, e in it['dates']:
            sd, ed = date.fromisoformat(s), date.fromisoformat(e)
            if ed >= TODAY and sd <= limit:
                upcoming.append({'start': s, 'end': e, 'title': it['title'], 'link': it['link'],
                                 'desc': it['desc'], 'source': it['source'], 'img': it['img'],
                                 'keywords': it['keywords']})
                break  # 1記事1件(最初に見つかった開催日)
    upcoming.sort(key=lambda x: (x['start'], x['end']))
    return {'id': store['id'], 'name': store['name'], 'area': store['area'],
            'feeds': store['feeds'], 'links': store['links'],
            'upcoming': upcoming, 'recent': recent[:RECENT_ARTICLES],
            'latest_pub': recent[0]['pub'] if recent else None,
            'fetched': len(items)}


def main():
    data = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M'), 'today': TODAY.isoformat(),
            'stores': [build_store(s) for s in SRC['stores']]}
    json.dump(data, open(os.path.join(HERE, 'data.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    tpl = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    page = tpl.replace('/*__DATA__*/null', json.dumps(data, ensure_ascii=False))
    open(os.path.join(HERE, 'index.html'), 'w', encoding='utf-8').write(page)
    for s in data['stores']:
        print(f"{s['name']}: 記事{s['fetched']}件 これから{len(s['upcoming'])}件")


if __name__ == '__main__':
    main()

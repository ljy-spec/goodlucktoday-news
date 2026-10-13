"""Standalone collector: fixed public news feeds to four small headline snapshots."""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 3_000_000
MAX_SNAPSHOT_BYTES = 100_000
LOCALES = ('ko', 'en', 'ja', 'es')
FEEDS = {
    'ko': 'https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko',
    'en': 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en',
    'ja': 'https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja',
    'es': 'https://news.google.com/rss?hl=es-419&gl=MX&ceid=MX:es-419',
}
LANGUAGES = {'ko': 'ko', 'en': 'en-US', 'ja': 'ja', 'es': 'es-419'}
NAMES = {'ko': '구글 뉴스', 'en': 'Google News', 'ja': 'Google ニュース', 'es': 'Google Noticias'}
HOSTS = {'naver': 'n.news.naver.com', 'nate': 'news.nate.com', 'google': 'news.google.com'}
HOMEPAGES = {'naver': 'https://news.naver.com/', 'nate': 'https://news.nate.com/', 'google': 'https://news.google.com/'}


def sources(locale):
    if locale not in LOCALES:
        raise ValueError('Unsupported language')
    return ('naver', 'nate', 'google') if locale == 'ko' else ('google',)


def target(locale, source):
    if source not in sources(locale):
        raise ValueError('Unknown source')
    return FEEDS[locale] if source == 'google' else HOMEPAGES[source]


class RejectRedirects(HTTPRedirectHandler):
    def http_error_302(self, request, response, code, message, headers):
        response.close()
        raise ValueError('News redirects are not allowed')
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class Headlines(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.source, self.current, self.title_tag, self.items = source, None, None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.current = {'url': urljoin(HOMEPAGES[self.source], attrs.get('href', '')), 'parts': []}
        if (self.source == 'nate' and tag == 'h2') or (self.source == 'naver' and 'cnf_news_title' in attrs.get('class', '').split()):
            self.title_tag = tag

    def handle_data(self, data):
        if self.current is not None and self.title_tag:
            self.current['parts'].append(data)

    def handle_endtag(self, tag):
        if tag == self.title_tag:
            self.title_tag = None
        if tag == 'a' and self.current is not None:
            self.items.append({'title': ''.join(self.current['parts']), 'url': self.current['url']})
            self.current = None


def normalize(source, items):
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not isinstance(item.get('url'), str):
            raise ValueError('Invalid headline')
        url = urlsplit(item['url'])
        title = ' '.join(item['title'].split())[:300]
        if title and len(item['url']) <= 2048 and url.scheme == 'https' and url.netloc == HOSTS[source] and item['url'] not in seen:
            seen.add(item['url'])
            result.append({'title': title, 'url': item['url']})
        if len(result) == 8:
            break
    return result


def parse_news(locale, source, body):
    target(locale, source)
    if source == 'google':
        import xml.etree.ElementTree as ET
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', body, re.I):
            raise ValueError('Unsupported XML declaration')
        root = ET.fromstring(body)
        if root.findtext('./channel/language', '').lower() != LANGUAGES[locale].lower():
            raise ValueError('Unexpected feed language')
        items = [{'title': item.findtext('title', ''), 'url': item.findtext('link', '')} for item in root.findall('./channel/item')]
    else:
        if source == 'nate':
            start = body.find('<div class="mainContents">')
            end = body.find('<!-- //mainContents -->', start)
            if start < 0 or end < 0:
                raise ValueError('Main headlines not found')
            body = body[start:end]
        # shortcut: Korean portals depend on markup, switch to licensed feeds if it changes.
        parser = Headlines(source)
        parser.feed(body)
        items = parser.items
    return normalize(source, items)


def read_news(locale, source):
    request = Request(target(locale, source), headers={'User-Agent': 'GoodLuckToday-News/1.0'})
    with build_opener(RejectRedirects()).open(request, timeout=8) as response:
        if int(response.headers.get('Content-Length', 0)) > MAX_BYTES:
            raise ValueError('News response too large')
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('News response too large')
        charset = response.headers.get_content_charset() or ('euc-kr' if source == 'nate' else 'utf-8')
    return parse_news(locale, source, raw.decode(charset))


def fetch_news(locale, source):
    target(locale, source)
    # The process deadline also bounds DNS and trickling headers or bodies.
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--fetch-news', locale, source],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=10,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    items = json.loads(result.stdout)
    if not isinstance(items, list) or len(items) > 8:
        raise ValueError('Invalid worker output')
    safe = normalize(source, items)
    if len(safe) != len(items):
        raise ValueError('Unsafe worker output')
    return safe


def collect_news(locale, fetch=fetch_news):
    def read(source):
        try:
            items = fetch(locale, source)
        except Exception:
            items = []
        name = NAMES[locale] if source == 'google' else '네이버' if source == 'naver' else '네이트'
        return {'id': source, 'name': name, 'homepage': HOMEPAGES[source], 'status': 'ok' if items else 'unavailable', 'items': items}
    with ThreadPoolExecutor(max_workers=3) as pool:
        entries = list(pool.map(read, sources(locale)))
    return {'locale': locale, 'fetchedAt': datetime.now(timezone.utc).isoformat(), 'sources': entries}


def self_check():
    for locale in LOCALES:
        xml = f'<rss><channel><language>{LANGUAGES[locale]}</language><item><title>Safe &amp; title</title><link>https://news.google.com/rss/articles/ok</link></item><item><title>Unsafe</title><link>https://evil.example/</link></item></channel></rss>'
        assert parse_news(locale, 'google', xml) == [{'title': 'Safe & title', 'url': 'https://news.google.com/rss/articles/ok'}]
        try:
            parse_news(locale, 'google', xml.replace(LANGUAGES[locale], 'wrong-language'))
        except ValueError:
            pass
        else:
            raise AssertionError('Wrong feed language accepted')
    try:
        parse_news('en', 'google', '<!DOCTYPE rss [<!ENTITY x "bad">]><rss/>')
    except ValueError:
        pass
    else:
        raise AssertionError('DTD accepted')
    assert not normalize('google', [{'title': 'Bad', 'url': 'https://user@news.google.com/private'}])
    snapshot = collect_news('ko', lambda locale, source: [] if source != 'google' else [{'title': 'Safe', 'url': 'https://news.google.com/rss/articles/ok'}])
    assert [entry['status'] for entry in snapshot['sources']] == ['unavailable', 'unavailable', 'ok']
    assert set(snapshot) == {'locale', 'fetchedAt', 'sources'}
    assert all(set(item) == {'title', 'url'} for source in snapshot['sources'] for item in source['items'])
    print('Collector self-check passed')


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--self-test':
        self_check()
    elif len(sys.argv) == 4 and sys.argv[1] == '--fetch-news':
        sys.stdout.buffer.write(json.dumps(read_news(sys.argv[2], sys.argv[3]), ensure_ascii=False).encode('utf-8'))
    elif len(sys.argv) == 2:
        snapshots = [collect_news(locale) for locale in LOCALES]
        if not any(source['items'] for data in snapshots for source in data['sources']):
            raise SystemExit('All news sources unavailable; keeping previous publication')
        output = Path(sys.argv[1])
        output.mkdir(parents=True, exist_ok=True)
        for data in snapshots:
            payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(payload) > MAX_SNAPSHOT_BYTES:
                raise ValueError('News snapshot too large')
            (output / f"{data['locale']}.json").write_bytes(payload)
        print('Wrote four public headline snapshots')
    else:
        raise SystemExit('Usage: collect-news.py OUTPUT_DIRECTORY | --self-test')

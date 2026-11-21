"""Chat's "How it searched" list shows the model's own tool calls. They are the model's words, not ours: everything in a
step must reach the page escaped, whatever shape the model sent (a list, a single value, text with markup in it).

Runs chat.js's stepText with app.js's esc under Node. Needs Node on PATH; skipped without it. Never packaged."""
import html.parser, json, re, shutil, subprocess, unittest, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which('node')
RUN = r"""
const fs = require('fs');
const chat = fs.readFileSync(process.argv[1], 'utf8'), app = fs.readFileSync(process.argv[2], 'utf8');
const esc = app.split('\n').find(l => /^const esc\s*=/.test(l));
const i = chat.indexOf('function stepText('), j = chat.indexOf('\n  function srcHref');
const stepText = new Function(esc + '\n' + chat.slice(i, j) + '\nreturn stepText;')();
const out = JSON.parse(process.argv[3]).map(s => { try { return stepText(s) } catch (e) { return 'THROW ' + e.message } });
process.stdout.write(JSON.stringify(out));
"""
# srcCards with its helpers from app.js (esc, fileUrl); ico, clock and here are stand-ins.
CARDS = r"""
const fs = require('fs');
const chat = fs.readFileSync(process.argv[1], 'utf8'), app = fs.readFileSync(process.argv[2], 'utf8');
const line = re => app.split('\n').find(l => re.test(l));
const helpers = [line(/^const esc\s*=/), line(/^const fileUrl\s*=/), "const ico = n => '<svg>' + n + '</svg>';",
  'const clock = t => String(t);', "const here = () => '#/ask';"].join('\n');
const i = chat.indexOf('function srcHref('), j = chat.indexOf('\n  /* markdown-lite');
const srcCards = new Function(helpers + '\n' + chat.slice(i, j) + '\nreturn srcCards;')();
process.stdout.write(JSON.stringify(JSON.parse(process.argv[3]).map(s => srcCards([s], false))));
"""
# srcFilter, the type buttons above an answer's sources, from the same part of chat.js. Each input is one answer's list.
FILTER = r"""
const fs = require('fs');
const chat = fs.readFileSync(process.argv[1], 'utf8'), app = fs.readFileSync(process.argv[2], 'utf8');
const line = re => app.split('\n').find(l => re.test(l));
const helpers = [line(/^const esc\s*=/), line(/^const fileUrl\s*=/), "const ico = n => '<svg>' + n + '</svg>';",
  'const clock = t => String(t);', "const here = () => '#/ask';"].join('\n');
const i = chat.indexOf('function srcHref('), j = chat.indexOf('\n  /* markdown-lite');
const srcFilter = new Function(helpers + '\n' + chat.slice(i, j) + '\nreturn srcFilter;')();
process.stdout.write(JSON.stringify(JSON.parse(process.argv[3]).map(list => srcFilter(list))));
"""


class Anchors(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.anchors.append(dict(attrs))


@unittest.skipUnless(NODE, 'needs Node')
class Steps(unittest.TestCase):
    def text(self, steps):
        r = subprocess.run([NODE, '-e', RUN, str(ROOT / 'web/chat.js'), str(ROOT / 'web/app.js'), json.dumps(steps)],
                           capture_output=True, text=True, check=True,
                           encoding='utf-8')  # Node writes UTF-8; Windows would read cp1252
        return json.loads(r.stdout)

    def test_read_pages_is_escaped_whatever_the_model_sent(self):
        got = self.text([
            {'name': 'read_pages', 'args': {'doc_id': 'x', 'pages': [3, 4]}, 'summary': {'title': 'FEMA'}},
            {'name': 'read_pages', 'args': {'doc_id': 'x', 'pages': ['<img src=x onerror=alert(1)>']}, 'summary': {'title': 'T'}},
            {'name': 'read_pages', 'args': {'doc_id': 'x', 'pages': '"><svg onload=alert(1)>'}, 'summary': {'title': 'T'}},
            {'name': 'read_pages', 'args': {'doc_id': 'x', 'pages': 7}, 'summary': {'title': 'T'}},
        ])
        self.assertEqual(got[0], 'Read FEMA, pages 3, 4')
        self.assertEqual(got[1], 'Read T, page &lt;img src=x onerror=alert(1)&gt;')
        self.assertEqual(got[2], 'Read T, page &quot;&gt;&lt;svg onload=alert(1)&gt;')
        self.assertEqual(got[3], 'Read T, page 7')
        for t in got:
            self.assertNotIn('<', t)

    def test_odd_shapes_neither_throw_nor_leak(self):
        # JSON can carry objects whose toString is not a function; converting them would throw and blank the answer.
        odd = [{'toString': 'x'}, {'a': 1}, [1, 2], None, True]
        got = self.text([
            {'name': 'read_pages', 'args': {'doc_id': 'x', 'pages': odd + [5, '6']}, 'summary': {'title': 'T'}},
            {'name': 'read_pages', 'args': {'doc_id': 'x', 'pages': {'toString': 'x'}}, 'summary': {'title': 'T'}},
            {'name': 'lookup_place', 'args': {'query': 'clinic', 'pages': {'toString': 'x'}}, 'summary': {}},
            {'name': 'search_library', 'args': {'query': 'water', 'pages': {'toString': 'x'}}, 'summary': {'count': 1}},
            {'name': 'view_page', 'args': {'doc_id': 'x', 'page': 3, 'pages': {'toString': 'x'}}, 'summary': {'title': 'T'}},
        ])
        for t in got:
            self.assertFalse(t.startswith('THROW'), t)
            self.assertNotIn('<', t)
        self.assertEqual(got[0], 'Read T, pages 5, 6')
        self.assertEqual(got[1], 'Read T, page ')
        self.assertEqual(got[2], 'Looked up “clinic” on the offline map')
        self.assertEqual(got[3], 'Searched “water” — 1 passage')
        self.assertEqual(got[4], 'Looked at the picture of T, page 3')

    def test_odd_values_in_other_arguments(self):
        bad = {'toString': 'x'}
        got = self.text([
            {'name': 'search_library', 'args': {'query': bad, 'kind': bad}, 'summary': {'count': 2}},
            {'name': 'lookup_place', 'args': {'query': bad}, 'summary': {}},
            {'name': 'view_page', 'args': {'doc_id': bad, 'page': bad}, 'summary': {'title': 'T'}},
            {'name': 'read_pages', 'args': {'doc_id': bad, 'pages': [2]}, 'summary': {}},
            {'name': 'search_library', 'args': 'not an object', 'summary': {'count': 0}},
            {'name': 'search_library', 'args': {'query': '<b>x</b>', 'kind': 'video'}, 'summary': {'count': 1}},
        ])
        for t in got:
            self.assertFalse(t.startswith('THROW'), t)
            self.assertNotIn('<', t)
        self.assertEqual(got[0], 'Searched “” — 2 passages')
        self.assertEqual(got[2], 'Looked at the picture of T, page ')
        self.assertEqual(got[3], 'Read , page 2')
        self.assertEqual(got[5], 'Searched “&lt;b&gt;x&lt;/b&gt;” (videos) — 1 passage')

    def test_encyclopedia_steps_are_escaped(self):
        got = self.text([
            {'name': 'search_encyclopedia', 'args': {'query': 'cholera'}, 'summary': {'count': 2}},
            {'name': 'search_encyclopedia', 'args': {'query': '<b>x</b>'}, 'summary': {'count': 1}},
            {'name': 'read_encyclopedia', 'args': {'article': 'zim/mdwiki.zim/Cholera'}, 'summary': {'title': 'Cholera'}},
            {'name': 'read_encyclopedia', 'args': {'article': '<img src=x>'}, 'summary': {'error': 'unknown'}},
            {'name': 'read_encyclopedia', 'args': {'article': '"><svg onload=alert(1)>'}, 'summary': {'title': '"><svg onload=alert(1)>'}},
        ])
        for t in got:
            self.assertFalse(t.startswith('THROW'), t)
            self.assertNotIn('<', t)
        self.assertEqual(got[0], 'Searched the encyclopedias for “cholera” — 2 articles')
        self.assertEqual(got[1], 'Searched the encyclopedias for “&lt;b&gt;x&lt;/b&gt;” — 1 article')
        self.assertEqual(got[2], 'Read the encyclopedia article “Cholera”')
        self.assertEqual(got[3], 'Tried to read an unknown encyclopedia article')
        self.assertEqual(got[4], 'Read the encyclopedia article “&quot;&gt;&lt;svg onload=alert(1)&gt;”')

    def test_encyclopedia_odd_argument_shapes(self):
        bad = {'toString': 'x'}
        got = self.text([
            {'name': 'search_encyclopedia', 'args': {'query': bad}, 'summary': {'count': 0}},
            {'name': 'read_encyclopedia', 'args': {'article': bad}, 'summary': {}},
            {'name': 'search_encyclopedia', 'args': 'not an object', 'summary': {'count': 0}},
        ])
        for t in got:
            self.assertFalse(t.startswith('THROW'), t)
            self.assertNotIn('<', t)
        self.assertEqual(got[0], 'Searched the encyclopedias for “” — 0 articles')
        self.assertEqual(got[1], 'Read the encyclopedia article “”')
        self.assertEqual(got[2], 'Searched the encyclopedias for “” — 0 articles')


# Article names straight from a ZIM file: a ? would cut the address, a bare % makes decodeURIComponent throw, a " would end the attribute.
ODD_ARTICLES = ('Why?', '100%_Juice', 'X"onmouseover="alert(1)')
ZIM = 'appropedia_en_all_maxi_2026-02.zim'


@unittest.skipUnless(NODE, 'needs Node')
class SourceCards(unittest.TestCase):
    def cards(self, rows):
        r = subprocess.run([NODE, '-e', CARDS, str(ROOT / 'web/chat.js'), str(ROOT / 'web/app.js'), json.dumps(rows)],
                           capture_output=True, text=True, check=True, encoding='utf-8')
        return json.loads(r.stdout)

    def check_link(self, card, article):
        p = Anchors()
        p.feed(card)
        self.assertEqual(len(p.anchors), 1, card)
        a = p.anchors[0]
        self.assertEqual(set(a), {'class', 'href', 'id', 'data-type'}, card)   # nothing like onmouseover got in
        self.assertEqual(a['data-type'], 'wiki', card)
        href = a['href']
        self.assertTrue(href.startswith('#/wiki/'), href)
        # As app.js parse() reads it: the address ends at the first ?, then each part is decoded.
        path, _, qs = href[1:].partition('?')
        self.assertTrue(qs.startswith('from='), href)
        self.assertIsNone(re.search(r'%(?![0-9A-Fa-f]{2})', path), href)   # decodeURIComponent would throw
        parts = [urllib.parse.unquote(x, errors='strict') for x in path.split('/') if x]
        self.assertEqual(parts, ['wiki', ZIM, article])

    def test_encyclopedia_card_links_use_the_encoded_path(self):
        rows = [{'kind': 'zim', 'key': f'S{n}', 'title': t, 'zim': f'zim/{ZIM}/{t}', 'doc_id': f'zim/{ZIM}/{t}',
                 # as encyclopedias.ui_path makes it, which the search hit and wiki_row carry
                 'path': 'zim/' + urllib.parse.quote(ZIM) + '/' + urllib.parse.quote(t, safe='/'),
                 'publisher': 'Appropedia', 'trust': 'Community-written encyclopedia', 'excerpt': 'x'}
                for n, t in enumerate(ODD_ARTICLES, 1)]
        for card, t in zip(self.cards(rows), ODD_ARTICLES):
            self.check_link(card, t)
            self.assertNotIn('onmouseover="', card)

    def test_a_row_without_a_path_is_encoded_from_its_key(self):
        rows = [{'kind': 'zim', 'key': 'S1', 'title': t, 'zim': f'zim/{ZIM}/{t}', 'doc_id': f'zim/{ZIM}/{t}'} for t in ODD_ARTICLES]
        for card, t in zip(self.cards(rows), ODD_ARTICLES):
            self.check_link(card, t)

    def test_other_cards_keep_their_links(self):
        got = self.cards([{'kind': 'pdf', 'key': 'S1', 'title': 'FEMA', 'doc_id': 'fema water', 'page': 3},
                          {'kind': 'place', 'key': 'M1', 'title': 'Clinic "A"', 'lat': 1.5, 'lon': 2.5}])
        p = Anchors()
        p.feed(got[0] + got[1])
        self.assertEqual(p.anchors[0]['href'], '#/read/fema%20water?p=3&from=%23%2Fask')
        self.assertEqual(p.anchors[1]['href'], '#/map?lat=1.5&lon=2.5&z=16&pin=Clinic%20%22A%22')

    def test_each_card_says_its_type(self):
        got = self.cards([{'kind': 'pdf', 'key': 'S1', 'title': 'A', 'doc_id': 'a', 'page': 3},
                          {'kind': 'html', 'key': 'S2', 'title': 'B', 'doc_id': 'b'},
                          {'kind': 'video', 'key': 'S3', 'title': 'C', 'doc_id': 'c', 't': 12},
                          {'kind': 'zim', 'key': 'S4', 'title': 'D', 'zim': f'zim/{ZIM}/D'},
                          {'kind': 'place', 'key': 'M1', 'title': 'E', 'lat': 1, 'lon': 2}])
        types = []
        for card in got:
            p = Anchors()
            p.feed(card)
            types.append(p.anchors[0]['data-type'])
        self.assertEqual(types, ['page', 'page', 'video', 'wiki', 'place'])


class Buttons(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons, self.group, self._at = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'div' and dict(attrs).get('class') == 'srcfilter':
            self.group = dict(attrs)
        if tag == 'button':
            self._at = dict(attrs)
            self.buttons.append([self._at, ''])

    def handle_endtag(self, tag):
        if tag == 'button':
            self._at = None

    def handle_data(self, data):
        if self._at is not None:
            self.buttons[-1][1] += data


@unittest.skipUnless(NODE, 'needs Node')
class SourceFilter(unittest.TestCase):
    def filters(self, lists):
        r = subprocess.run([NODE, '-e', FILTER, str(ROOT / 'web/chat.js'), str(ROOT / 'web/app.js'), json.dumps(lists)],
                           capture_output=True, text=True, check=True, encoding='utf-8')
        return json.loads(r.stdout)

    def parse(self, h):
        p = Buttons()
        p.feed(h)
        return p

    def test_one_button_per_type_present_with_counts_in_a_fixed_order(self):
        mixed = [{'kind': 'place', 'key': 'M1'}, {'kind': 'pdf', 'key': 'S1'}, {'kind': 'video', 'key': 'S2'},
                 {'kind': 'pdf', 'key': 'S3'}, {'kind': 'pptx', 'key': 'S4'}, {'kind': 'zim', 'key': 'S5'}]
        p = self.parse(self.filters([mixed])[0])
        self.assertEqual(p.group.get('role'), 'group')
        self.assertTrue(p.group.get('aria-label'))
        got = [(a['data-type'], a['aria-pressed'], ' '.join(t.split())) for a, t in p.buttons]
        self.assertEqual(got, [('', 'true', 'All 6'), ('page', 'false', 'Pages 3'), ('video', 'false', 'Videos 1'),
                               ('wiki', 'false', 'Encyclopedias 1'), ('place', 'false', 'Places 1')])
        for a, _ in p.buttons:
            self.assertEqual(a.get('type'), 'button')   # inside the chat page, never a submit

    def test_types_with_no_sources_get_no_button(self):
        p = self.parse(self.filters([[{'kind': 'pdf', 'key': 'S1'}, {'kind': 'video', 'key': 'S2'}, {'kind': 'video', 'key': 'S3'}]])[0])
        self.assertEqual([a['data-type'] for a, _ in p.buttons], ['', 'page', 'video'])

    def test_no_filter_when_there_is_nothing_to_choose(self):
        got = self.filters([[], [{'kind': 'pdf', 'key': 'S1'}], [{'kind': 'pdf', 'key': 'S1'}, {'kind': 'html', 'key': 'S2'}],
                            [{'kind': 'video', 'key': 'S1'}, {'kind': 'video', 'key': 'S2'}]])
        self.assertEqual(got, ['', '', '', ''])

    def test_an_encyclopedia_row_without_a_kind_counts_as_an_encyclopedia(self):
        p = self.parse(self.filters([[{'key': 'S1', 'zim': f'zim/{ZIM}/X'}, {'kind': 'pdf', 'key': 'S2'}]])[0])
        self.assertEqual([a['data-type'] for a, _ in p.buttons], ['', 'page', 'wiki'])


if __name__ == '__main__':
    unittest.main()

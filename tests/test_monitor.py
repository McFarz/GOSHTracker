import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape
import monitor as m


def feed(rows=(), pages=1, total=None):
    if total is None:
        total = len(rows)
    body = ''.join('<vacancyDetails>' + ''.join(f'<{k}>{escape(str(v))}</{k}>' for k, v in row.items()) + '</vacancyDetails>' for row in rows)
    return f'<nhsJobs><totalPages>{pages}</totalPages><totalResults>{total}</totalResults>{body}</nhsJobs>'.encode()


def job(identifier='one', title='Assistant Psychologist', employer=m.EMPLOYER):
    return dict(id=identifier, title=title, employer=employer, url='https://www.jobs.nhs.uk/candidate/jobadvert/C9271-26-0001')


def response(xml, status=200):
    r = Mock(status_code=status, content=xml, url=m.FEED_URL)
    r.raise_for_status.return_value = None
    return r


class MonitorTests(unittest.TestCase):
    def test_vacancy_web_url_variants(self):
        links = [
            'https://beta.jobs.nhs.uk/candidate/jobadvert/C9271-26-0001',
            'http://www.jobs.nhs.uk/candidate/jobadvert/C9271-26-0001',
            'https://apps.trac.jobs/job-advert/12345',
            'https://www.healthjobsuk.com/job/UK/London/Psychology-v12345',
            'https://recruitment.example.org/vacancy/12345',
            '/candidate/jobadvert/C9271-26-0001',
            '//beta.jobs.nhs.uk/candidate/jobadvert/C9271-26-0001',
        ]
        for value in links:
            with self.subTest(value=value):
                row = job()
                row['url'] = value
                parsed = m.parse_page(feed([row]))[2][0]['url']
                self.assertTrue(parsed.startswith(('https://', 'http://')))

    def test_invalid_vacancy_links_rejected(self):
        links = ('javascript:alert(1)', 'file:///tmp/a',
                 'https://user:password@example.org/a', 'https://example.org:abc/a',
                 'https://example.org/a b', 'https:///missing-host', 'https://example.org\\a')
        for value in links:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.normalize_vacancy_url(value)

    def test_recognized_empty_feed(self):
        self.assertEqual(m.parse_page(feed(pages=0)), (0, 0, []))

    def test_html_and_inconsistent_counts_fail(self):
        for xml in (b'<html>Forbidden</html>', feed(pages=1, total=1), feed([job()], total=0)):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                m.parse_page(xml)

    def test_missing_required_field_fails(self):
        row = job()
        del row['employer']
        with self.assertRaises(ValueError):
            m.parse_page(feed([row]))

    def test_all_pages_employer_and_title_filters(self):
        rows = [job('1'), job('2', 'Assistant Clinical Psychologist'), job('3', 'Psychology Assistant'), job('4', 'Psychological Assistant'), job('5', 'Assistant Neuropsychologist'), job('6', 'Assistant Psychologist - Neuropsychology'), job('7', 'Senior Psychologist'), job('8', employer='Another NHS Trust')]
        with patch.object(m.requests, 'Session') as s:
            client = s.return_value.__enter__.return_value
            client.get.side_effect = [response(feed(rows[:4], pages=2, total=8)), response(feed(rows[4:], pages=2, total=8))]
            found = m.fetch_jobs()
            self.assertEqual(set(found), {'nhsjobs:1', 'nhsjobs:2', 'nhsjobs:3', 'nhsjobs:4'})
            self.assertEqual(client.get.call_count, 2)

    def test_403_preserves_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'state.json'
            path.write_text('{"seen": {"previous": {}}}')
            before = path.read_bytes()
            with patch.object(m, 'STATE_FILE', path), patch.object(m.requests, 'Session') as s, patch.object(m, 'email_message') as send:
                s.return_value.__enter__.return_value.get.return_value = response(b'blocked', 403)
                with self.assertRaisesRegex(RuntimeError, '403'):
                    m.main([])
                send.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_partial_fetch_and_repeated_pages_fail(self):
        for second in (response(feed([job()], pages=2, total=2)), response(feed([job('2')], pages=2, total=3))):
            with self.subTest(), patch.object(m.requests, 'Session') as s:
                s.return_value.__enter__.return_value.get.side_effect = [response(feed([job()], pages=2, total=2)), second]
                with self.assertRaises(ValueError):
                    m.fetch_jobs()

    def test_test_feed_never_sends_or_saves(self):
        with patch.object(m, 'fetch_jobs', return_value={'nhsjobs:one': job()}), patch.object(m, 'email_message') as send, patch.object(m, 'save_state') as save:
            self.assertEqual(m.main(['--test-feed']), 0)
            send.assert_not_called()
            save.assert_not_called()

    def test_email_failure_leaves_job_unseen(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'state.json'
            path.write_text(json.dumps({'seen': {}, 'source': 'nhsjobs-xml-v2'}))
            before = path.read_bytes()
            with patch.object(m, 'STATE_FILE', path), patch.object(m, 'fetch_jobs', return_value={'nhsjobs:one': job()}), patch.object(m, 'email_message', side_effect=RuntimeError('SMTP failure')):
                with self.assertRaisesRegex(RuntimeError, 'SMTP'):
                    m.main([])
            self.assertEqual(path.read_bytes(), before)

    def test_source_migration_baselines_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'state.json'
            path.write_text(json.dumps({'seen': {'123': {'title': 'Old Trac entry'}}}))
            with patch.object(m, 'STATE_FILE', path), patch.object(m, 'fetch_jobs', return_value={'nhsjobs:one': job()}), patch.object(m, 'email_message') as send:
                m.main([])
                state = json.loads(path.read_text())
                self.assertEqual(state['source'], 'nhsjobs-xml-v2')
                self.assertIn('123', state['seen'])
                self.assertIn('nhsjobs:one', state['seen'])
                m.main([])
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

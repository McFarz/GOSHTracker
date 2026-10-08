import unittest
from monitor import parse_jobs, extract_title, TITLE_PATTERN
from bs4 import BeautifulSoup

HTML = '''<html><body><h2>Vacancies</h2><h3>Find jobs in...</h3>
<a href="/job/UK/London/London/Great_Ormond_Street_Hospital_Children_NHS_Foundation_Trust/Psychology/Psychology-v8110690">Assistant Neuropsychologist Band 4 Great Ormond Street Hospital for Children NHS Foundation Trust London Salary: £...</a>
<a href="/job/UK/London/London/Great_Ormond_Street_Hospital_Children_NHS_Foundation_Trust/Psychology/Psychology-v8900012">Senior Clinical Psychologist Band 8a Great Ormond Street Hospital for Children NHS Foundation Trust London Salary: £...</a>
<a href="/job/UK/London/London/Great_Ormond_Street_Hospital_Children_NHS_Foundation_Trust/Psychology/Psychology-v8900013">Assistant Clinical Psychologist NHS AfC: Band 4 Great Ormond Street Hospital for Children NHS Foundation Trust London</a>
</body></html>'''

class MonitorTest(unittest.TestCase):
    def test_matches_assistant_only(self):
        data = parse_jobs(HTML)
        self.assertEqual(set(data), {'8900013'})
        self.assertEqual(data['8900013']['title'], 'Assistant Clinical Psychologist')

    def test_excludes_neuropsychologist(self):
        self.assertFalse(TITLE_PATTERN.search('Assistant Neuropsychologist'))
        self.assertFalse(TITLE_PATTERN.search('Assistant Clinical Neuropsychologist'))

    def test_empty_listing_is_valid(self):
        self.assertEqual(parse_jobs('<h2>Vacancies</h2><h3>Find jobs in...</h3>'), {})

    def test_unexpected_page_fails(self):
        with self.assertRaises(ValueError):
            parse_jobs('<h1>Access denied</h1>')

    def test_excludes_qualified_roles(self):
        self.assertFalse(TITLE_PATTERN.search('Senior Clinical Psychologist'))
        self.assertFalse(TITLE_PATTERN.search('Research Assistant'))

if __name__ == '__main__':
    unittest.main()

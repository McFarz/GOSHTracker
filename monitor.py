"""GOSH assistant psychology vacancy watcher using the official NHS Jobs XML API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import sys
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse

import requests
from defusedxml import ElementTree as ET

FEED_URL = 'https://www.jobs.nhs.uk/api/v1/search_xml'
STATE_FILE = Path(os.getenv('STATE_FILE', 'seen_jobs.json'))
EMPLOYER = 'Great Ormond Street Hospital for Children NHS Foundation Trust'
# Matches the title ONLY. Broader research assistants and qualified psychologists are excluded.
TITLE_PATTERN = re.compile(
    r'\b(?:assistant\s+(?:clinical\s+)?psychologist|'
    r'(?:clinical\s+)?psychology\s+assistant|'
    r'psychological\s+assistant)\b', re.I
)
LOG = logging.getLogger('gosh-monitor')


def normalize_vacancy_url(value):
    """The documented url is a web link, not a fixed NHS Jobs path."""
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError(f'Malformed vacancy URL in feed: {value!r}')
    if not value.startswith(('/', 'http://', 'https://')):
        raise ValueError(f'Unsupported vacancy URL format in feed: {value!r}')
    normalized = urljoin(FEED_URL, value) if value.startswith('/') else value
    try:
        link = urlparse(normalized)
        valid = (link.scheme in ('http', 'https') and link.hostname
                 and '.' in link.hostname and not link.username and not link.password
                 and link.port in (None, 80, 443))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f'Malformed vacancy URL in feed: {value!r}')
    return normalized


def parse_page(xml):
    """Validate the documented XML wrapper, counts and required vacancy fields."""
    root = ET.fromstring(xml)
    # Ignore namespace prefixes while keeping field names case sensitive.
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
    if root.tag != 'nhsJobs':
        raise ValueError('Expected nhsJobs XML, received a different response')
    try:
        pages = int(root.findtext('totalPages', ''))
        total = int(root.findtext('totalResults', ''))
    except ValueError as exc:
        raise ValueError('Missing or invalid feed result counts') from exc
    rows = []
    for element in root.findall('vacancyDetails'):
        job = {n.tag: (n.text or '').strip() for n in element}
        if not all(job.get(k) for k in ('id', 'title', 'employer', 'url')):
            raise ValueError('Vacancy missing id, title, employer or url')
        job['url'] = normalize_vacancy_url(job['url'])
        # Namespace source IDs: old Trac numeric IDs belong to another system.
        job['id'] = 'nhsjobs:' + job['id']
        rows.append(job)
    if total < 0 or pages < 0 or (total == 0 and (rows or pages > 1)) or (total > 0 and (pages == 0 or not rows)):
        raise ValueError('Feed counts and vacancy rows disagree')
    return pages, total, rows


def fetch_jobs():
    """Retrieve every page before filtering; a partial fetch never advances state."""
    code = os.getenv('NHS_EMPLOYER_CODE', '').strip()
    params = {'limit': 100, 'page': 1, 'sort': 'publicationDateDesc'}
    if code:
        params.update(employerCode=code, externalOnly='true')
    else:
        params['employer'] = EMPLOYER
    all_jobs = {}
    expected = None
    with requests.Session() as session:
        session.headers.update({'User-Agent': 'GOSH-Vacancy-Monitor/2.1', 'Accept': 'application/xml'})
        for page in range(1, 101):
            params['page'] = page
            response = session.get(FEED_URL, params=params, timeout=30)
            LOG.info('Feed page %d: HTTP %d', page, response.status_code)
            if response.status_code == 403:
                raise RuntimeError('NHS Jobs XML API refused access (HTTP 403). No jobs were checked and state was not updated. Ask NHSBSA whether automated access from GitHub Actions is supported; do not treat this as an empty feed.')
            response.raise_for_status()
            if urlparse(response.url).hostname != 'www.jobs.nhs.uk':
                raise ValueError('Feed redirected outside NHS Jobs')
            pages, total, rows = parse_page(response.content)
            if pages > 100:
                raise ValueError('More than 100 pages returned; employer filtering may have failed')
            if expected is None:
                expected = (pages, total)
            elif expected != (pages, total):
                raise ValueError('Feed changed during pagination; retry on the next run')
            for job in rows:
                if job['id'] in all_jobs:
                    raise ValueError('Repeated vacancy ID across feed pages; refusing incomplete results')
                all_jobs[job['id']] = job
            if page >= max(1, pages):
                break
    if len(all_jobs) != expected[1]:
        raise ValueError('Retrieved vacancy count does not match totalResults')
    gosh = {key: job for key, job in all_jobs.items()
            if ' '.join(job['employer'].casefold().split()) == EMPLOYER.casefold()}
    if all_jobs and not gosh:
        raise ValueError('Feed returned vacancies but none have the GOSH employer name; verify the employer filter/code')
    matches = {key: job for key, job in gosh.items()
               if TITLE_PATTERN.search(job['title']) and not re.search(r'neuro[\s-]*psycholog', job['title'], re.I)}
    LOG.info('Successfully parsed NHS Jobs XML: %d vacancies, %d GOSH vacancies, %d matching titles', len(all_jobs), len(gosh), len(matches))
    return matches


def load_state():
    if not STATE_FILE.exists():
        return None
    state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or not isinstance(state.get('seen'), dict):
        raise ValueError('Invalid state file format')
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(STATE_FILE)


def email_message(subject, body):
    required = ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'ALERT_TO')
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError('Missing email settings: ' + ', '.join(missing))
    host = os.environ['SMTP_HOST']
    port = int(os.getenv('SMTP_PORT') or '465')
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.getenv('ALERT_FROM') or os.environ['SMTP_USER']
    msg['To'] = os.environ['ALERT_TO']
    msg.set_content(body)
    if os.getenv('SMTP_STARTTLS', '').lower() == 'true':
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.starttls()
            client.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
            client.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=30) as client:
            client.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
            client.send_message(msg)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notify-existing', action='store_true', help='Send matches on first run, instead of silently creating a baseline')
    parser.add_argument('--dry-run', action='store_true', help='Show matched jobs without sending or changing state')
    parser.add_argument('--test-feed', action='store_true', help='Fetch and validate all feed pages without email or state changes')
    parser.add_argument('--test-email', action='store_true', help='Send one test email and exit')
    args = parser.parse_args(argv)
    if args.test_email:
        email_message('GOSH monitor test', 'The GOSH job monitor email configuration works.')
        print('Test email sent')
        return 0

    current = fetch_jobs()  # Important: do not save state on HTTP/parse failure
    print(f'Scanned NHS Jobs XML feed: {len(current)} matching jobs')
    for job in current.values():
        print(f"  {job['title']} ({job['id']}): {job['url']}")
    if args.dry_run or args.test_feed:
        return 0
    state = load_state()
    first_run = state is None or state.get('source') != 'nhsjobs-xml-v2'
    seen = state['seen'] if state else {}
    new_jobs = [job for vid, job in current.items() if vid not in seen]
    if first_run and not args.notify_existing:
        LOG.info('First run: storing %d current matches without notification', len(new_jobs))
    elif new_jobs:
        body = ('New Great Ormond Street Hospital assistant psychology vacancies found.\n\n' +
                '\n\n'.join(f"{j['title']}\n{j['url']}\nVacancy ID: {j['id']}\nSalary: {j.get('salary', 'Not supplied')}\nContract: {j.get('type', 'Not supplied')}\nClosing date: {j.get('closeDate', 'Not supplied')}" for j in new_jobs) +
                '\n\nCheck the listing promptly: vacancies may close early.')
        # Only mark new jobs as seen after successful notification.
        email_message(f'GOSH vacancy alert: {len(new_jobs)} new role(s)', body)
        LOG.info('Sent alert for %d new roles', len(new_jobs))
    else:
        LOG.info('No new matching roles')
    now = datetime.now(timezone.utc).isoformat()
    for vid, job in current.items():
        if vid not in seen:
            seen[vid] = {'title': job['title'], 'url': job['url'], 'first_seen': now}
    save_state({'seen': seen, 'last_successful_check': now, 'source': 'nhsjobs-xml-v2'})
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        sys.exit(main())
    except Exception:
        LOG.exception('Monitor failed; state not advanced. Check GitHub Actions logs.')
        sys.exit(1)

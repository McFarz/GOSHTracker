"""GOSH assistant psychology vacancy watcher (public Trac employer listing)."""
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
from bs4 import BeautifulSoup

BASE_URL = 'https://www.healthjobsuk.com/jobs_emp?emp=54'
FALLBACK_URL = 'https://www.nhsjobs.com/jobs_emp?emp=54'
STATE_FILE = Path(os.getenv('STATE_FILE', 'seen_jobs.json'))
EMPLOYER = 'Great Ormond Street Hospital for Children NHS Foundation Trust'
# Matches the title ONLY. Broader research assistants and qualified psychologists are excluded.
TITLE_PATTERN = re.compile(
    r'\b(?:assistant\s+(?:clinical\s+)?psychologist|'
    r'(?:clinical\s+)?psychology\s+assistant|'
    r'psychological\s+assistant)\b', re.I
)
BAND_PATTERN = re.compile(
    r'\s+(?:(?:NHS\s+)?AfC:\s*)?Band\s+\d+[a-z]?\b|'
    r'\s+NHS\s+Medical\s*&\s*Dental:', re.I
)
LOG = logging.getLogger('gosh-monitor')


def extract_title(anchor):
    """Extract a title from employer-list links, avoiding band/employer/metadata."""
    # First prefer heading or a dedicated title node if the site exposes one.
    specific = anchor.select_one('h2, h3, h4, .job-title, .vacancy-title')
    text = specific.get_text(' ', strip=True) if specific else anchor.get_text(' ', strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    # Typical Trac link text: 'Assistant Psychologist Band 4 Great Ormond...'
    text = BAND_PATTERN.split(text, maxsplit=1)[0]
    if EMPLOYER.casefold() in text.casefold():
        text = re.split(re.escape(EMPLOYER), text, flags=re.I)[0].strip()
    return text


def parse_jobs(html, base_url=BASE_URL):
    """Return unique title-matching vacancies; fail when listing structure is unrecognisable."""
    soup = BeautifulSoup(html, 'html.parser')
    result = {}
    valid_links = 0
    for anchor in soup.select('a[href]'):
        href = anchor.get('href', '')
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.hostname not in ('www.nhsjobs.com', 'nhsjobs.com', 'www.healthjobsuk.com', 'healthjobsuk.com'):
            continue
        # Trac links contain a stable vacancy ID, e.g. ...-v8110690
        match = re.search(r'-v(\d+)(?:/)?$', parsed.path, re.I)
        if not match:
            continue
        valid_links += 1
        title = extract_title(anchor)
        if TITLE_PATTERN.search(title) and not re.search(r'neuro\s*psycholog', title, re.I):
            vid = match.group(1)
            result[vid] = {'id': vid, 'title': title, 'url': full_url.split('?')[0]}
    # An empty list with a legitimate listing page is okay, but do not silently
    # accept a block/challenge or redesign as 'no jobs'.
    page_text = soup.get_text(' ', strip=True).casefold()
    if not valid_links and not ('vacancies' in page_text and 'find jobs' in page_text):
        raise ValueError('No recognizable Trac vacancy links or employer vacancy listing detected')
    return result


def fetch_jobs():
    """Try both public Trac listing domains; never treat blocking as zero vacancies."""
    errors = []
    with requests.Session() as session:
        session.headers.update({
            'User-Agent': 'GOSH-Assistant-Psychology-Watcher/1.1 (personal vacancy notifier)',
            'Accept': 'text/html,application/xhtml+xml',
        })
        for url in (BASE_URL, FALLBACK_URL):
            try:
                resp = session.get(url, timeout=30)
                resp.raise_for_status()
                if urlparse(resp.url).hostname not in (
                    'www.healthjobsuk.com', 'healthjobsuk.com',
                    'www.nhsjobs.com', 'nhsjobs.com',
                ):
                    raise ValueError('Listing redirected outside expected recruitment sites')
                jobs = parse_jobs(resp.text, base_url=resp.url)
                LOG.info('Successfully checked %s', resp.url)
                return jobs
            except (requests.RequestException, ValueError) as exc:
                errors.append(f'{url}: {type(exc).__name__}: {exc}')
                LOG.warning('Listing source unavailable: %s', errors[-1])
    raise RuntimeError(
        'Both public Trac sources failed. The recruitment service may block '
        'automated GitHub Actions requests (HTTP 403). This is not an email '
        'configuration issue. No vacancies have been checked and state was not '
        'updated.\n' + '\n'.join(errors)
    )


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
    port = int(os.getenv('SMTP_PORT', '465'))
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.getenv('ALERT_FROM', os.environ['SMTP_USER'])
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
    parser.add_argument('--test-email', action='store_true', help='Send one test email and exit')
    args = parser.parse_args(argv)
    if args.test_email:
        email_message('GOSH monitor test', 'The GOSH job monitor email configuration works.')
        print('Test email sent')
        return 0

    current = fetch_jobs()  # Important: do not save state on HTTP/parse failure
    print(f'Scanned GOSH Trac employer listing: {len(current)} matching jobs')
    for job in current.values():
        print(f"  {job['title']} ({job['id']}): {job['url']}")
    if args.dry_run:
        return 0
    state = load_state()
    first_run = state is None
    seen = state['seen'] if state else {}
    new_jobs = [job for vid, job in current.items() if vid not in seen]
    if first_run and not args.notify_existing:
        LOG.info('First run: storing %d current matches without notification', len(new_jobs))
    elif new_jobs:
        body = ('New Great Ormond Street Hospital assistant psychology vacancies found.\n\n' +
                '\n\n'.join(f"{j['title']}\n{j['url']}\nVacancy ID: {j['id']}" for j in new_jobs) +
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
    save_state({'seen': seen, 'last_successful_check': now})
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        sys.exit(main())
    except Exception:
        LOG.exception('Monitor failed; state not advanced. Check GitHub Actions logs.')
        sys.exit(1)

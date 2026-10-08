# GOSH job monitor v2.1: official NHS Jobs XML feed

GitHub Actions reached the XML endpoint with HTTP 200, but v2 rejected a
vacancy URL because it imposed an undocumented hostname/path requirement.
Version 2.1 accepts normal HTTP/HTTPS vacancy links, including third-party
recruitment systems, and resolves root-relative links against NHS Jobs. It
still rejects malformed links and non-web schemes. The actual rejected URL
was not present in the user's log, so this fix needs another live test.

Replace monitor.py to apply the fix. Replace tests/test_monitor.py as well to
include the two new regression tests (11 tests total). Keep the existing
requirements.txt, workflow, email secrets and seen_jobs.json.

Start a new workflow run on main with mode test-feed. A successful run should
log Successfully parsed NHS Jobs XML. This test does not send email or save state.

## Update your existing GOSHTracker repository

Keep your email secrets and seen_jobs.json. Replace/add these files at the
repository root (do not upload the enclosing gosh-job-monitor-v2 folder):

- monitor.py
- requirements.txt
- tests/test_monitor.py
- .github/workflows/monitor.yml

The workflow uses the existing name. If GitHub's upload interface omits .github,
open the existing workflow file and paste in the supplied YAML. All four files
must be present because the workflow now runs the tests.

1. Commit the changes to the default branch, normally main.
2. Actions > GOSH assistant psychologist vacancies > Run workflow.
3. Select main and choose mode `test-feed`.
4. Start a NEW run, rather than re-running a previous commit.
5. Read the `Check for vacancies` log.

A successful response logs `Successfully parsed NHS Jobs XML`, showing the
number retrieved, the number with the exact GOSH employer name, and the number
of matching titles. A recognized zero-result XML feed is valid, but does not
prove the employer filter is correct or that all GOSH vacancies are syndicated.
Confirm results against NHS Jobs before relying on it.

If the API returns 403 in GitHub Actions too, stop here. This version does not
bypass access controls or substitute another hostname. Ask NHS Jobs support at
nhsbsa.nhsjobs@nhsbsa.nhs.uk whether the Self-Serve XML API supports personal
monitoring from GitHub-hosted runners, and whether an approved access mechanism
is required. You must send that enquiry yourself.

After a successful feed test, select `test-email` to send a test message to your
configured ALERT_TO recipient. Then select `monitor` to create the new baseline.
Scheduled runs also use monitor mode. The schedule is active as soon as this
workflow is on the default branch, so it may create the baseline before your
manual run. On the first successful v2 monitoring run, current matching
vacancies are recorded without email. Future unseen IDs trigger an email.

Keep Settings > Actions > General > Workflow permissions set to permit writing
state. Branch protection may prevent the state commit; check the final workflow
step too. If it fails, future runs may repeat alerts.

## Documented API

Source: NHSBSA Self-Serve Job Adverts API V1.07, dated 1 May 2026:
https://www.nhsbsa.nhs.uk/about-nhs-jobs/nhs-jobs-integration-and-benefits

Specification linked there:
https://www.nhsbsa.nhs.uk/sites/default/files/2026-05/NHS%20Jobs%20Self-Serve%20Job%20Adverts%20API%20V1.07_0.docx

Endpoint: https://www.jobs.nhs.uk/api/v1/search_xml

Default query parameters:

- employer=Great Ormond Street Hospital for Children NHS Foundation Trust
- limit=100
- page=1, followed by subsequent pages as needed
- sort=publicationDateDesc

The code also checks each vacancy's employer name locally. It uses title-only
matching for Assistant Psychologist, Assistant Clinical Psychologist,
Psychology Assistant (including Clinical Psychology Assistant), and
Psychological Assistant. Titles containing neuropsychology are excluded.
Research assistant and qualified psychologist titles do not match.

Optional: NHS_EMPLOYER_CODE can be set as a repository Actions variable if you
obtain GOSH's confirmed NHS Jobs employer code from support or GOSH recruitment.
When supplied, it replaces the employer-name query and adds externalOnly=true.
No employer code is guessed from vacancy reference numbers or Trac's emp=54.
This version does not request internal-only vacancies.

The XML includes title, ID, employer, URL, salary, contract type, and closing
date. The last three are included in emails when supplied. Band and working
pattern are not dedicated fields in this XML response, so they are not inferred.

## How the Python program works

1. Request the XML feed with requests.
2. Parse it with defusedxml and validate the wrapper, counts, required fields,
   and web vacancy links. The program does not fetch those links.
3. Fetch all pages. Reject repeated IDs, inconsistent totals or incomplete results.
4. Filter by exact employer name, then title.
5. Compare the NHS Jobs IDs with seen_jobs.json.
6. Send one email listing the new matches.
7. Save state atomically only after successful processing/email submission.

NHS Jobs IDs are prefixed `nhsjobs:` to avoid collisions with Trac's numeric IDs.
Existing Trac history is retained. Source migration baselines once to avoid
re-alerting on roles already present at the transition.

State is not updated on HTTP errors, unexpected XML, incomplete pagination or
email errors. SMTP submission does not guarantee inbox delivery. If the process
crashes after SMTP accepts a message but before state is saved, an alert can be
repeated on the next run. Re-publishing the same vacancy ID does not trigger a
new alert. New IDs may represent re-advertised roles.

## Local commands

From the extracted project directory, with Python 3.12:

```powershell
py -m pip install -r requirements.txt
py -m unittest discover -s tests -v
py monitor.py --test-feed
```

`--test-feed` and `--dry-run` never send email or write state. `--test-email`
uses your existing SMTP environment settings. Normal `py monitor.py` creates
state and sends alerts for new matches. `--notify-existing` sends currently
matching jobs when creating the initial v2 baseline; it does not resend seen IDs.

## Limits and learning next steps

- Both HTML hosts returned 403 in the user's GitHub Actions logs. That establishes
  refusal for those requests, not the site's exact blocking policy.
- The API returned HTTP 200 in GitHub Actions, but a full parse still needs testing.
- There is no failure email or daily heartbeat in this version. Errors appear in
  Actions logs. Configure GitHub's workflow-failure notifications separately.
- Cron requests runs at minutes 7 and 37 of each hour; execution can be delayed.
- Fetching every GOSH vacancy before title filtering avoids relying on the API's
  keyword search, which also searches descriptions.
- A public feed cannot prove coverage of staff-only or un-syndicated vacancies.

The useful lesson is to distinguish transport success (HTTP 200), valid data
(XML and pagination), correct filtering, and successful notification. A green
workflow alone does not establish all four.

# GOSH Assistant Psychology Job Monitor

Checks the **Great Ormond Street Hospital for Children NHS Foundation Trust** employer listing on Trac's `nhsjobs.com` website every ~30 minutes and emails when a *new* assistant psychology job appears.

Source: https://www.nhsjobs.com/jobs_emp?emp=54

It matches titles such as **Assistant Psychologist**, **Assistant Clinical Psychologist**, **Psychology Assistant**, and **Psychological Assistant**. It deliberately excludes **Assistant Neuropsychologist**, other neuropsychology titles, general psychologist, senior psychologist, and research assistant jobs. Edit `TITLE_PATTERN` in `monitor.py` to change the match criteria.

## Setup (GitHub Actions, recommended)

1. Create a **private GitHub repository**, e.g. `gosh-job-monitor`.
2. Upload the contents of this folder to its repository root, including the `.github/workflows/monitor.yml` file (GitHub may hide dot-prefixed folders in some upload interfaces; Git from a terminal is reliable).
3. Go to the repository's **Settings → Secrets and variables → Actions → New repository secret**. Add:

   | Secret | Example / purpose |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` for Gmail |
   | `SMTP_PORT` | `465` for Gmail SSL |
   | `SMTP_USER` | the email account sending alerts |
   | `SMTP_PASSWORD` | SMTP app password, **not** the regular mailbox password |
   | `ALERT_TO` | recipient email address |

   Optional: `ALERT_FROM` (custom from address; sender policy may prevent changing it), `SMTP_STARTTLS` (`true` if using port `587`, otherwise omit). With Gmail, use 2-Step Verification and generate an **app password** if that option is available. Alternatively use another provider with SMTP credentials. **Never commit secrets to GitHub.**
4. In the repository's **Actions** tab, enable workflows if prompted. In **Settings → Actions → General**, make sure workflow permissions allow **Read and write permissions**. The workflow commits `seen_jobs.json` to keep a durable record of vacancies already detected.
5. In **Actions → GOSH assistant psychologist vacancies → Run workflow**, run it manually for the first time. This **creates the baseline without sending alerts** for jobs already present. Watch the workflow log; success means the site was parsed and the state was saved.
6. Future runs check every 30 minutes (scheduling can be delayed). **GitHub may automatically disable scheduled workflows in inactive public repos**; a private repo is recommended for privacy, but still occasionally verify checks are running. Free-tier minutes and service limits may apply.

**For testing your email immediately**, run `python monitor.py --test-email` locally with the variables configured. Or temporarily run `python monitor.py --notify-existing` locally on a *fresh* database, if a matching vacancy is available. Note: a first normal run is intentionally silent.

## Running on Windows locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "465"
$env:SMTP_USER = "your-sender@gmail.com"
$env:SMTP_PASSWORD = "your-app-password"
$env:ALERT_TO = "your-recipient@example.com"
python monitor.py --test-email
python monitor.py --dry-run
python monitor.py
```

Run regularly with Windows Task Scheduler if not using GitHub Actions. Keep the same working directory so `seen_jobs.json` is preserved.

## Details and limitations

- **Public vacancies only.** Does **not** search GOSH's internal staff-only portal or private vacancies; it does not use employee access. If some roles are genuinely posted *only* internally, this tool cannot detect them.
- Only the **Trac `nhsjobs.com` employer vacancy list** is monitored in this version; it does not independently crawl NHS Jobs, the charity site, or other sources. This keeps false matches low and requests modest.
- Vacancy IDs are deduplicated across runs. **If email sending fails, the job is not marked seen**, so the next run will retry.
- The first normal run seeds existing jobs rather than alerting. Run with `--notify-existing` if you want alerts for current matching jobs on your first run.
- Site redesign, anti-bot controls, or changes in listing structure can disrupt checks. Errors fail the GitHub Actions run rather than silently marking jobs absent. Check Actions run history and enable GitHub workflow-failure notifications in your account settings.
- This is a **targeted monitor**, not a guaranteed immediate notification service. There can be site publication delays, parsing failures, GitHub schedule delays, or brief postings between checks.
- Respect website terms and robots restrictions; the script makes one request per scheduled check with a 30-second timeout.
- To change the interval, edit `cron` in `.github/workflows/monitor.yml`. Current schedule: minutes 7 and 37 of each hour, in UTC.

## Tests

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The parser has offline fixture tests. **A live website check was not possible in the build environment**, so please run `python monitor.py --dry-run` locally or the manual GitHub workflow and inspect the output to confirm current site compatibility.

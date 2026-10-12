# Setting up Sa'ei on your PC

Sa'ei runs on your own computer. You need **Python 3.10+** (3.11 recommended)
and one AI model, either local or with an API key.

- [1. Install](#1-install)
- [2. Choose an AI model](#2-choose-an-ai-model)
- [3. Add your CV and run](#3-add-your-cv-and-run)
- [4. Pick your job boards](#4-pick-your-job-boards)
- [5. Connect Gmail (optional)](#5-connect-gmail-optional)
- [Enabling auto-submit](#enabling-auto-submit)
- [For developers](#for-developers)
- [Cautions](#cautions)

---

## 1. Install

**Quickest:** on Windows double-click `run.bat`; on macOS or Linux run
`./run.sh`. The first run creates a virtual environment, installs the
packages, creates your `.env` file and opens the dashboard at
http://localhost:8001/.

**By hand**, if you prefer:

```bash
# with venv
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# or with conda
conda env create -f environment.yml
conda activate job-agent
```

Then copy the settings file:

```bash
cp .env.example .env
```

That's enough to start. Sa'ei begins in **dry run** (`DRY_RUN=true`), so
nothing is sent until you turn it off.

## 2. Choose an AI model

Pick one. You can change it any time in **Settings → AI model**, which saves
your choice and key to `.env`.

| Provider | What you need | Good to know |
|---|---|---|
| **Ollama** (local) | Install [Ollama](https://ollama.com), then `ollama pull qwen3:4b` | Free, private, no limits. Speed depends on your PC. |
| **Groq** | `GROQ_API_KEY` | Free and very fast. Limited to 200k tokens a day. |
| **Gemini** | `GEMINI_API_KEY` | Free tier. The same key can also do embeddings. |
| **OpenRouter** | `OPENROUTER_API_KEY` | Many models behind one key. Free models allow 50 requests a day. |
| **Anthropic** | `ANTHROPIC_API_KEY` | Claude models. Paid per token, no daily limit. |
| **OpenAI** | `OPENAI_API_KEY` | GPT models. Paid per token, no daily limit. |

For better matching, also pull an embedding model:
`ollama pull nomic-embed-text` (or `qwen3-embedding:4b` for Arabic postings).
Sa'ei still works without one.

## 3. Add your CV and run

Start the app, open the **CV** page and upload your CV (PDF or Word). Your
profile is filled from it; check it on the **Profile** page.

| To... | Run |
|---|---|
| Start everything (Windows) | `run.bat`, or `run_conda.bat` / `run_conda_quick.bat` with conda |
| Start everything (macOS / Linux) | `./run.sh` |
| Start only the API and dashboard | `uvicorn api:app --port 8001`, then open http://localhost:8001/ |
| Run one search now | `python jobs/daily_run.py` |
| Prepare an application from one link | `python jobs/apply_from_link.py "<job posting URL>"` |

`run_conda_quick.bat` skips the dependency update, so it starts faster. Use
`run_conda.bat` after pulling changes that add packages.

While it runs, Sa'ei searches every day at 8:00, emails a report at 20:00
and sends a news digest on Mondays at 9:00. It stops when your PC sleeps;
see [deployment.md](deployment.md) to run it on a server.

## 4. Pick your job boards

Out of the box, Sa'ei searches remote-job feeds plus any Greenhouse and
Lever boards listed in `.env`. More boards appear under **Search → Manage
sources**, each with an **Add** button:

| Board | Covers |
|---|---|
| LinkedIn | Worldwide (public job search) |
| Wellfound | Startups, worldwide |
| SimplyHired | Worldwide |
| Wuzzuf, Tanqeeb | Egypt |
| Bayt | Gulf / MENA |
| GulfTalent | Gulf |

You can also paste any company's careers page. To add boards automatically on
first run, list them in `SEARCH_DEFAULT_BOARDS` in `.env`.

## 5. Connect Gmail (optional)

Gmail sends the applications you approve, your daily report and the weekly
news. Open **Settings → Email & reports** (from your name at the bottom of
the sidebar) and click **Sign in with Google**. Sa'ei can only send email;
it can't read your inbox. Details: [google-sign-in.md](google-sign-in.md).

Reports go to your own address unless you set another one. While dry run is
on, nothing is sent; reports only appear on the **Reports** page.

---

## Enabling auto-submit

By default Sa'ei prepares applications and waits for you to send them.
Automatic submission is switched on one employer at a time:

1. Look at that board's application form; each Greenhouse or Lever board can
   have its own fields.
2. Implement its form in `agents/apply_agent.py` (`auto_submit_greenhouse`
   is a deliberate placeholder).
3. Test with `DRY_RUN=true`, then with one real application.
4. Add `greenhouse:<board>` or `lever:<company>` to `WHITELISTED_SOURCES`.

Related settings, under **Settings → Autonomy**: `AUTO_APPLY_MODE`
(`off`, `whitelist` or `any`), `FIT_THRESHOLD` (default 70%: below it the CV
is tailored) and `AUTO_APPLY_ON_TAILORED_SCORE`.

## For developers

```bash
pytest                              # the test suite
python -m bench.report              # matching precision/recall and score regression
python -m bench.report --calibrate  # re-tune semantic thresholds after changing the embedding model
```

The dashboard's styles are pre-compiled into `frontend/tailwind.css`. After
changing Tailwind classes, rebuild from `frontend/`:

```bash
npx tailwindcss@3 -c tailwind.config.js -i tailwind.src.css -o tailwind.css --minify
```

How the pieces fit together: [how-it-works.md](how-it-works.md) and
[architecture.md](architecture.md).

## Cautions

- **Follow each job board's terms.** Sa'ei uses public APIs and pages that
  allow access. Indeed blocks automated requests and isn't supported.
- **Go slowly.** Too many automated requests can get an IP or account
  flagged. Start with drafts, then allow automatic sending one board at a time.
- **You're responsible for what's sent in your name.** Review tailored CVs
  before they go out.

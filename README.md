# Autonomous AI Job Search Agent

A Streamlit app for discovering remote job listings, ranking them against a target role and optional profile keywords, and managing a reviewable shortlist.

## Features

- Search remote listings by role phrase, location, seniority, and employment type.
- Rank results with an explainable keyword-alignment heuristic. The score is not a hiring prediction.
- Apply optional profile keywords locally. They remain in the app session and are not sent to the listing provider.
- Save jobs to a session-only shortlist and export saved listings as CSV.
- Open each source listing on Himalayas for review and application.

## Data source and privacy

Listings come from the public [Himalayas Remote Jobs API](https://himalayas.app/docs/remote-jobs-api). Role phrase, location, seniority, and employment type are sent to Himalayas to retrieve results. Profile keywords are applied locally. The app does not collect a CV, submit applications, or predict hiring outcomes. Listings can expire or change, so check the source posting before applying.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Project links

- GitHub: https://github.com/sarajsinghal/autonomous-ai-job-search-agent
- Live app: https://autonomous-ai-job-search-agent.streamlit.app/

## Attribution

Job listings are provided by Himalayas. This app links users to the Himalayas listing page and uses the `applicationLink` supplied by the API.

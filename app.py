from __future__ import annotations

import csv
import html
import io
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import requests
import streamlit as st

API_URL = "https://himalayas.app/jobs/api/search"

st.set_page_config(
    page_title="Autonomous AI Job Search Agent",
    page_icon="🔎",
    layout="wide",
)


class _TextExtractor(HTMLParser):
    """Extract readable text from the API's sanitized HTML descriptions."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def clean_html(value: str | None) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()


@st.cache_data(ttl="6h", max_entries=64, show_spinner=False)
def fetch_jobs(query: str, country: str, seniority: str, employment_type: str, worldwide: bool, page: int) -> tuple[list[dict[str, Any]], int]:
    params: dict[str, str] = {"sort": "recent", "page": str(page)}
    if query:
        params["q"] = query
    if worldwide:
        params["worldwide"] = "true"
    elif country and country != "Any country":
        params["country"] = country
    if seniority != "Any level":
        params["seniority"] = seniority
    if employment_type != "Any type":
        params["employment_type"] = employment_type

    response = requests.get(
        API_URL,
        params=params,
        headers={"Accept": "application/json", "User-Agent": "AutonomousJobSearchAgent/1.0"},
        timeout=20,
    )
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "").strip()
        raise RuntimeError(f"The job source is rate-limiting requests. Try again later{f' (retry after {retry_after} seconds)' if retry_after.isdigit() else ''}.")
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        raise RuntimeError("The job source returned an unexpected response.")
    return jobs, int(payload.get("totalCount", len(jobs)))


def word_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9+#.]+", value.lower()) if len(token) > 1}


def rank_job(job: dict[str, Any], role_terms: set[str], skill_terms: set[str]) -> tuple[int, list[str]]:
    title = str(job.get("title", ""))
    description = clean_html(str(job.get("excerpt", "")) + " " + str(job.get("description", "")))
    categories = " ".join(job.get("categories", []) + job.get("parentCategories", [])) if isinstance(job.get("categories"), list) else ""
    title_tokens = word_tokens(title)
    content_tokens = word_tokens(f"{title} {job.get('excerpt', '')} {categories} {description[:5000]}")

    role_coverage = len(role_terms & title_tokens) / len(role_terms) if role_terms else 0.0
    role_context = len(role_terms & content_tokens) / len(role_terms) if role_terms else 0.0
    skill_coverage = len(skill_terms & content_tokens) / len(skill_terms) if skill_terms else 0.0
    score = round(100 * (0.55 * role_coverage + 0.25 * role_context + 0.20 * skill_coverage))

    reasons: list[str] = []
    matched_title = sorted(role_terms & title_tokens)
    matched_skills = sorted(skill_terms & content_tokens)
    if matched_title:
        reasons.append("Role terms in title: " + ", ".join(matched_title))
    if matched_skills:
        reasons.append("Profile terms found: " + ", ".join(matched_skills[:8]))
    if not reasons:
        reasons.append("Returned by the selected role, location, and seniority filters")
    return score, reasons


def location_text(job: dict[str, Any]) -> str:
    restrictions = job.get("locationRestrictions") or []
    names = [str(item.get("name", "")) for item in restrictions if isinstance(item, dict) and item.get("name")]
    zones = job.get("timezoneRestrictions") or []
    if not names:
        return "Worldwide"
    location = ", ".join(names[:5])
    if len(names) > 5:
        location += f" +{len(names) - 5} more"
    if zones:
        location += " · Time zones: " + ", ".join(map(str, zones[:4]))
    return location


def salary_text(job: dict[str, Any]) -> str:
    low, high = job.get("minSalary"), job.get("maxSalary")
    currency = str(job.get("currency") or "")
    period = str(job.get("salaryPeriod") or "annual").lower()
    if low is None and high is None:
        return "Salary not listed"
    def amount(value: Any) -> str:
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)
    if low is not None and high is not None:
        pay = f"{amount(low)}–{amount(high)} {currency}".strip()
    else:
        pay = f"{amount(low if low is not None else high)} {currency}".strip()
    return f"{pay} / {period}"


def posted_text(job: dict[str, Any]) -> str:
    stamp = job.get("pubDate")
    if not stamp:
        return "Posting date unavailable"
    try:
        posted = datetime.fromtimestamp(float(stamp), tz=timezone.utc)
        return "Updated " + posted.strftime("%d %b %Y")
    except (TypeError, ValueError, OSError):
        return "Posting date unavailable"


def export_csv(jobs: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    columns = ["title", "company", "location", "seniority", "employment_type", "salary", "posted", "match_score", "application_link"]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for job in jobs:
        writer.writerow({
            "title": job.get("title", ""),
            "company": job.get("companyName", ""),
            "location": location_text(job),
            "seniority": ", ".join(job.get("seniority", []) or []),
            "employment_type": job.get("employmentType", ""),
            "salary": salary_text(job),
            "posted": posted_text(job),
            "match_score": job.get("_match_score", 0),
            "application_link": job.get("applicationLink", ""),
        })
    return output.getvalue().encode("utf-8-sig")


st.title("Autonomous AI Job Search Agent")
st.markdown("Find remote roles, rank them against your target position and skills, and keep a shortlist in one place.")
st.caption("The agent searches and ranks listings. You review each opportunity and choose whether to apply.")

st.info(
    "Listings are provided by Himalayas and may be refreshed up to once per day. Keyword alignment is a transparent heuristic, not a hiring prediction. Every application opens the Himalayas application page.",
    title="How the agent works",
)

if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "search_total" not in st.session_state:
    st.session_state.search_total = 0
if "shortlist" not in st.session_state:
    st.session_state.shortlist = {}
if "has_searched" not in st.session_state:
    st.session_state.has_searched = False

with st.form("job_search_form", border=True):
    st.subheader("Set your search")
    role_col, country_col = st.columns([2, 1])
    with role_col:
        role_query = st.text_input(
            "Target role or search phrase",
            placeholder="For example, AI evaluator or data analyst",
            max_chars=120,
            key="role_query",
        )
    with country_col:
        country = st.selectbox(
            "Candidate location",
            ["India", "United States", "United Kingdom", "Canada", "Australia", "Germany", "Singapore", "Any country", "Worldwide only"],
            index=0,
            key="country_filter",
        )
    skills = st.text_input(
        "Skills or experience to prioritize",
        placeholder="For example, Hindi, SQL, customer support, prompt evaluation",
        max_chars=250,
        help="Used only to rank returned listings in this session; these terms are not sent to the jobs API.",
        key="skills_input",
    )
    filter_col, worldwide_col = st.columns([1, 1])
    with filter_col:
        seniority = st.selectbox(
            "Seniority",
            ["Any level", "Entry-level", "Mid-level", "Senior", "Manager", "Director", "Executive"],
            index=1,
            key="seniority_filter",
        )
    with worldwide_col:
        employment_type = st.selectbox(
            "Employment type",
            ["Any type", "Full Time", "Part Time", "Contractor", "Temporary", "Intern", "Volunteer"],
            index=0,
            key="employment_filter",
        )
    page_number = st.number_input("Result page", min_value=1, max_value=50, value=1, step=1, key="result_page")
    search_submitted = st.form_submit_button("Search jobs", type="primary", icon=":material/search:")

if search_submitted:
    q = role_query.strip()
    if not q:
        st.warning("Enter a target role or search phrase to start a search.", title="Add a search term")
    else:
        with st.spinner("Searching current remote listings…"):
            try:
                jobs, total = fetch_jobs(
                    q[:120],
                    "" if country in {"Any country", "Worldwide only"} else country,
                    seniority,
                    employment_type,
                    country == "Worldwide only",
                    int(page_number),
                )
                role_terms = word_tokens(q)
                skill_terms = word_tokens(skills)
                ranked = []
                for job in jobs:
                    score, reasons = rank_job(job, role_terms, skill_terms)
                    job = dict(job)
                    job["_match_score"] = score
                    job["_match_reasons"] = reasons
                    ranked.append(job)
                ranked.sort(key=lambda job: (int(job["_match_score"]), job.get("pubDate", 0)), reverse=True)
                st.session_state.search_results = ranked
                st.session_state.search_total = total
                st.session_state.has_searched = True
                st.session_state.search_message = None
            except requests.Timeout:
                st.session_state.search_message = "The job source took too long to respond. Please try again."
                st.session_state.has_searched = True
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                st.session_state.search_message = f"The job source could not complete this search{f' (HTTP {status})' if status else ''}. Please try again later."
                st.session_state.has_searched = True
            except (ValueError, RuntimeError) as exc:
                st.session_state.search_message = str(exc)
                st.session_state.has_searched = True

results, saved = st.tabs(["Matches", f"Shortlist ({len(st.session_state.shortlist)})"])
with results:
    if st.session_state.has_searched:
        if st.session_state.get("search_message"):
            st.error(st.session_state.search_message, title="Search unavailable")
        elif not st.session_state.search_results:
            st.info("No listings matched those filters. Try a broader role phrase or a different location.", title="No matches found")
        else:
            st.subheader(f"Ranked matches · {len(st.session_state.search_results)} shown")
            st.caption(f"The source reports {st.session_state.search_total:,} matching listings. Page {int(page_number)} is ranked locally against your search phrase and profile terms.")
            for index, job in enumerate(st.session_state.search_results):
                job_id = str(job.get("guid") or job.get("applicationLink") or f"job-{index}")
                with st.container(border=True):
                    title_col, score_col = st.columns([4, 1])
                    with title_col:
                        st.subheader(str(job.get("title") or "Untitled role"))
                        company = str(job.get("companyName") or "Company not listed")
                        st.write(f"**{company}** · {location_text(job)}")
                    with score_col:
                        st.metric("Keyword alignment", f"{job['_match_score']}%", border=True)
                    details = [posted_text(job), str(job.get("employmentType") or "Type not listed")]
                    seniority_value = job.get("seniority") or []
                    if seniority_value:
                        details.append(", ".join(map(str, seniority_value)))
                    st.caption(" · ".join(details) + " · " + salary_text(job))
                    if job.get("_match_reasons"):
                        st.caption("Match signals: " + "; ".join(job["_match_reasons"]))
                    summary = clean_html(str(job.get("excerpt") or job.get("description") or ""))
                    if summary:
                        st.write(summary[:420] + ("…" if len(summary) > 420 else ""))
                    action_col, save_col = st.columns([1, 1])
                    with action_col:
                        apply_url = job.get("applicationLink")
                        if apply_url and str(apply_url).startswith("https://"):
                            st.link_button("View and apply on Himalayas", str(apply_url), icon=":material/open_in_new:")
                    with save_col:
                        if job_id not in st.session_state.shortlist:
                            if st.button("Save to shortlist", key=f"save_{job_id}", icon=":material/bookmark_add:"):
                                st.session_state.shortlist[job_id] = job
                                st.rerun()
                        else:
                            st.caption("Saved to your shortlist")
    else:
        st.info("Enter a role or skill above and start a search. Your latest results will appear here.", title="Ready when you are")

with saved:
    st.subheader("Your shortlist")
    if not st.session_state.shortlist:
        st.info("Save a role from your matches to build a shortlist. Saved roles are kept only for this browser session.", title="No saved roles yet")
    else:
        shortlist_jobs = list(st.session_state.shortlist.values())
        st.caption(f"{len(shortlist_jobs)} saved role(s). Review each listing before applying; openings and requirements can change.")
        st.download_button(
            "Download shortlist as CSV",
            data=export_csv(shortlist_jobs),
            file_name="job_search_shortlist.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        for index, job in enumerate(shortlist_jobs):
            job_id = str(job.get("guid") or job.get("applicationLink") or f"saved-{index}")
            with st.container(border=True):
                st.subheader(str(job.get("title") or "Untitled role"))
                st.write(f"**{job.get('companyName') or 'Company not listed'}** · {location_text(job)}")
                st.caption(f"Keyword alignment: {job.get('_match_score', 0)}% · {salary_text(job)} · {posted_text(job)}")
                action_col, remove_col = st.columns([1, 1])
                with action_col:
                    if job.get("applicationLink") and str(job["applicationLink"]).startswith("https://"):
                        st.link_button("View and apply on Himalayas", str(job["applicationLink"]), icon=":material/open_in_new:")
                with remove_col:
                    if st.button("Remove", key=f"remove_{job_id}", icon=":material/bookmark_remove:"):
                        del st.session_state.shortlist[job_id]
                        st.rerun()

st.divider()
st.caption("Job data provided by Himalayas. Listings may close or change; verify the original posting before applying. Role and location search terms are sent to Himalayas. Skill terms are used only for local ranking in this session.")
st.markdown("[Job listings and source information · Himalayas](https://himalayas.app/)")


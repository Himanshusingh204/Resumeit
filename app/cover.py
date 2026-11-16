"""Optional cover letter (plan F). Same rule as the resume: only facts from the profile."""
import json

from . import ai, guard

SYSTEM = ("Write a short cover letter (under 180 words, 3 short paragraphs, plain text, no placeholders) for this "
          "candidate applying to this job. HARD RULES: use ONLY facts in the candidate profile. Never invent "
          "employers, titles, dates, degrees, tools, skills or numbers. If the job needs something the profile "
          "lacks, simply do not mention it. Sign with the candidate's name.")


def generate(profile, job):
    jd = f"{job['title']} at {job['company']}\n\n{job.get('description') or ''}"[:8000]
    text = ai.complete(SYSTEM, f"CANDIDATE PROFILE:\n{json.dumps(profile)}\n\nJOB:\n{jd}").strip()
    if len(text) < 80:
        raise ValueError("cover letter came back empty")
    new = guard.numbers(text) - guard._profile_numbers(profile)
    if new:
        raise ValueError("cover letter rejected, numbers not in your profile: " + ", ".join(sorted(new)))
    return text

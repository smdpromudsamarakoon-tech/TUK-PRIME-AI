"""Create a letter draft and open it in the user's default text editor.

Designed for voice requests such as: "Open Notepad and draft a letter asking
for my exam results so I can send them to my university."
"""
from __future__ import annotations

import platform
import re
import subprocess
from datetime import date
from pathlib import Path


def _default_exam_results_letter() -> str:
    return f"""[Your Full Name]
[Your Address]
[Your Email Address]
[Your Phone Number]
{date.today().strftime('%d %B %Y')}

The Examinations Officer
[School / Examination Authority]

Subject: Request for Official Examination Results

Dear Sir/Madam,

I am writing to kindly request my official examination results and, if available, an official results sheet or certificate. I require these documents for submission to my university as part of its admissions and academic verification process.

My details are as follows:
Full name: [Your Full Name]
Examination: [Examination Name, e.g., GCE Advanced Level]
Examination year: [Year]
Candidate / index number: [Index Number]
School: [School Name]

I would be grateful if you could advise me on the procedure, any required documents or fees, and the expected time for issuing the results. If the results can be sent directly to my university, please let me know what information you need from me.

Thank you for your time and assistance. I look forward to your response.

Yours faithfully,
[Your Full Name]
"""


def _open_editor(path: Path) -> tuple[bool, str]:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-a", "TextEdit", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            subprocess.Popen(["notepad.exe", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            opener = "xdg-open"
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def letter_writer(parameters=None, player=None, **_ignored) -> str:
    params = parameters or {}
    title = str(params.get("title") or "Exam Results Request Letter").strip()
    body = str(params.get("body") or "").strip()
    if not body:
        body = _default_exam_results_letter()
    # Keep generated filenames safe and avoid overwriting existing drafts.
    safe_title = re.sub(r"[^A-Za-z0-9 _-]", "", title).strip().replace(" ", "_") or "Letter_Draft"
    folder = Path.home() / "Documents" / "TUK_Letters"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safe_title}.txt"
        if path.exists():
            from datetime import datetime
            path = folder / f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path.write_text(body + "\n", encoding="utf-8")
    except Exception as exc:
        return f"I couldn't save the letter draft: {exc}"

    opened, error = _open_editor(path)
    if player:
        try:
            player.write_log(f"[letter_writer] Draft saved: {path}")
        except Exception:
            pass
    if opened:
        return f"I created the letter draft and opened it in your text editor. File: {path}. Review the placeholders and fill in your details before sending."
    return f"I saved the letter draft at {path}, but couldn't open the text editor: {error}"


TOOL = {
    "name": "letter_writer",
    "description": (
        "Draft and open a letter in the computer's text editor. Use when the user asks "
        "to open Notepad/TextEdit and write or generate a letter. For an exam-results "
        "request letter to send to a university, call with title 'Exam Results Request Letter' "
        "and leave body empty to create a formal editable template. If the user specifies "
        "the letter's content, provide that complete draft in body. Saves a .txt draft under "
        "Documents/TUK_Letters and opens it; it does not send the letter."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING", "description": "Short document title / filename"},
            "body": {"type": "STRING", "description": "Complete letter body to save; leave empty for the exam-results request template"},
        },
        "required": [],
    },
    "handler": letter_writer,
}

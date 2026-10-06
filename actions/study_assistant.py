"""Sri Lankan school study support: O/L, A/L, notes, quizzes, and revision plans."""
from __future__ import annotations

_LEVELS = {
    "ol": "Sri Lankan G.C.E. Ordinary Level (O/L), generally Grades 10–11",
    "o/l": "Sri Lankan G.C.E. Ordinary Level (O/L), generally Grades 10–11",
    "ordinary level": "Sri Lankan G.C.E. Ordinary Level (O/L), generally Grades 10–11",
    "al": "Sri Lankan G.C.E. Advanced Level (A/L), generally Grades 12–13",
    "a/l": "Sri Lankan G.C.E. Advanced Level (A/L), generally Grades 12–13",
    "advanced level": "Sri Lankan G.C.E. Advanced Level (A/L), generally Grades 12–13",
}

def study_assistant_action(parameters: dict, player=None, **_) -> str:
    level_raw = str(parameters.get("level") or "").strip().lower()
    level = _LEVELS.get(level_raw, "Sri Lankan school curriculum; clarify O/L or A/L if it matters")
    subject = str(parameters.get("subject") or "").strip()[:100]
    topic = str(parameters.get("topic") or "").strip()[:180]
    task = str(parameters.get("task") or "explain").strip().lower()
    language = str(parameters.get("language") or "match the user's latest message").strip()[:40]
    allowed_tasks = {"explain", "notes", "quiz", "revision_plan", "practice_questions", "study_plan"}
    if task not in allowed_tasks:
        task = "explain"
    if not subject or not topic:
        missing = "subject" if not subject else "topic"
        return f"Ask the student which {missing} they want to study. Offer Sinhala or English explanations, short notes, quizzes, and revision plans for Sri Lankan O/L and A/L."
    return (
        "STUDY MODE CONTEXT (use this to answer the student's original request; do not read this metadata aloud):\n"
        f"Curriculum context: {level}.\nSubject: {subject}.\nTopic: {topic}.\nTask: {task}.\nPreferred language: {language}.\n"
        "Teaching requirements: explain step-by-step at the stated exam level; define technical terms simply; use examples and practice questions; "
        "when asked for notes, structure them with headings and concise bullet points; for quizzes, ask one question at a time and wait for the student's answer; "
        "for revision plans, create realistic short sessions with breaks. Use Sinhala script when the user writes Sinhala, and understand Singlish. "
        "Do not claim that a question is from an official past paper unless a verified source was retrieved. If exact syllabus coverage or current exam requirements are uncertain, say so and recommend checking the official Department of Examinations or NIE material."
    )

TOOL = {
    "name": "sri_lankan_study_assistant",
    "description": (
        "Helps Sri Lankan students with O/L and A/L study. Use for syllabus-level explanations, Sinhala/English notes, quizzes, "
        "practice questions, and revision/study plans. Do not open a browser tab. Ask for the missing subject/topic if not provided. "
        "Never falsely label generated questions as official past-paper questions."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "level": {"type": "STRING", "description": "O/L, A/L, or school level"},
            "subject": {"type": "STRING", "description": "Subject such as Mathematics, Science, ICT, Biology, Accounting, Economics, Sinhala, English"},
            "topic": {"type": "STRING", "description": "Specific topic or question"},
            "task": {"type": "STRING", "description": "explain, notes, quiz, revision_plan, practice_questions, or study_plan"},
            "language": {"type": "STRING", "description": "Sinhala, English, or Singlish; default match latest user message"},
        },
        "required": [],
    },
    "handler": study_assistant_action,
}

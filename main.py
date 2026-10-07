import os
import json
import time
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import parser

app = FastAPI(title="MCQ Exam Engine", version="1.0.0")

EXAMS_FILE = "data_exams.json"
SUBMISSIONS_FILE = "data_submissions.json"

def load_json_data(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json_data(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Load sample paper text and answers on startup
with open("sample_number_system_30q.txt", "r", encoding="utf-8") as f:
    SAMPLE_Q_TEXT = f.read()

with open("sample_answer_key_30q.txt", "r", encoding="utf-8") as f:
    SAMPLE_ANS_TEXT = f.read()

@app.get("/api/sample-paper")
def get_sample_paper():
    """Returns parsed pre-loaded 30-question Number System test paper."""
    questions = parser.parse_question_paper(SAMPLE_Q_TEXT)
    ans_res = parser.parse_answer_key(SAMPLE_ANS_TEXT)
    answers = ans_res["answers"]
    explanations = ans_res["explanations"]

    for q in questions:
        q_num = q["number"]
        if q_num in answers:
            q["correctAnswer"] = answers[q_num]
        if q_num in explanations:
            q["explanation"] = explanations[q_num]

    return {
        "title": "LBSNAA - Civil Services Examination (30 Questions)",
        "questions": questions,
        "rawQuestionText": SAMPLE_Q_TEXT,
        "rawAnswerText": SAMPLE_ANS_TEXT,
        "answers": answers,
        "explanations": explanations
    }

@app.post("/api/parse-paper")
async def parse_paper_endpoint(
    file: Optional[UploadFile] = File(None),
    rawText: Optional[str] = Form(None)
):
    """
    Parses an uploaded TXT/DOCX/PDF file or raw text into structured MCQ questions.
    """
    text_content = ""
    if file:
        filename = file.filename
        content = await file.read()
        text_content = parser.extract_text_from_file(content, filename)
    elif rawText:
        text_content = rawText
    else:
        raise HTTPException(status_code=400, detail="No file or raw text provided.")

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="Extracted text is empty.")

    questions = parser.parse_question_paper(text_content)
    total_warnings = sum(len(q["parseWarnings"]) for q in questions)

    return {
        "questions": questions,
        "count": len(questions),
        "totalWarnings": total_warnings,
        "extractedLength": len(text_content)
    }

@app.post("/api/parse-answer-key")
async def parse_answer_key_endpoint(
    file: Optional[UploadFile] = File(None),
    rawText: Optional[str] = Form(None)
):
    """
    Parses answer key from TXT/DOCX/PDF file or raw text.
    """
    text_content = ""
    if file:
        filename = file.filename
        content = await file.read()
        text_content = parser.extract_text_from_file(content, filename)
    elif rawText:
        text_content = rawText
    else:
        raise HTTPException(status_code=400, detail="No file or raw text provided.")

    ans_res = parser.parse_answer_key(text_content)
    return ans_res

class ExamPublishPayload(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = "Computer Based Test Exam"
    durationMinutes: int = 30
    positiveMarks: float = 1.0
    negativeMarks: float = 0.0
    shuffle: bool = False
    questions: List[Dict[str, Any]]

@app.post("/api/exams")
def create_exam(payload: ExamPublishPayload):
    exams = load_json_data(EXAMS_FILE, {})
    exam_id = payload.id or f"exam_{int(time.time())}"
    exam_data = payload.dict()
    exam_data["id"] = exam_id
    exam_data["createdAt"] = time.time()
    
    exams[exam_id] = exam_data
    save_json_data(EXAMS_FILE, exams)
    return {"status": "success", "examId": exam_id, "exam": exam_data}

@app.get("/api/exams/{exam_id}")
def get_exam(exam_id: str):
    exams = load_json_data(EXAMS_FILE, {})
    if exam_id not in exams:
        # Check sample exam fallback
        if exam_id == "sample":
            sample = get_sample_paper()
            return {
                "id": "sample",
                "title": sample["title"],
                "description": "Benchmark Number System 30-Question Paper",
                "durationMinutes": 45,
                "positiveMarks": 1.0,
                "negativeMarks": 0.33,
                "shuffle": False,
                "questions": sample["questions"]
            }
        raise HTTPException(status_code=404, detail="Exam not found")
    return exams[exam_id]

class EvaluationPayload(BaseModel):
    examId: str
    candidateName: str
    rollNumber: str
    responses: Dict[str, str] # qId -> selectedKey (e.g. {"q1": "b", "q2": "a"})
    questionTimeSpent: Dict[str, float] # qId -> seconds
    markedForReview: List[str] = []
    totalTimeTaken: float # seconds

@app.post("/api/evaluate")
def evaluate_exam(payload: EvaluationPayload):
    exams = load_json_data(EXAMS_FILE, {})
    exam = exams.get(payload.examId)
    
    if not exam and payload.examId == "sample":
        sample = get_sample_paper()
        exam = {
            "id": "sample",
            "title": sample["title"],
            "positiveMarks": 1.0,
            "negativeMarks": 0.33,
            "questions": sample["questions"]
        }

    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found for evaluation.")

    questions = exam["questions"]
    pos_marks = float(exam.get("positiveMarks", 1.0))
    neg_marks = float(exam.get("negativeMarks", 0.0))

    total_questions = len(questions)
    attempted_count = 0
    correct_count = 0
    wrong_count = 0
    unattempted_count = 0
    score = 0.0
    max_score = total_questions * pos_marks

    error_questions = []
    question_evaluations = []

    for q in questions:
        q_id = q["id"]
        correct_key = (q.get("correctAnswer") or "").lower().strip()
        user_key = payload.responses.get(q_id, "").lower().strip()
        time_spent = payload.questionTimeSpent.get(q_id, 0.0)
        is_marked = q_id in payload.markedForReview

        status = "unattempted"
        is_correct = False

        if user_key:
            attempted_count += 1
            if user_key == correct_key:
                correct_count += 1
                score += pos_marks
                status = "correct"
                is_correct = True
            else:
                wrong_count += 1
                score -= neg_marks
                status = "wrong"
                is_correct = False
                
                # Add to error list
                error_questions.append({
                    "id": q_id,
                    "number": q["number"],
                    "stem": q["stem"],
                    "options": q["options"],
                    "userAnswer": user_key,
                    "correctAnswer": correct_key,
                    "explanation": q.get("explanation"),
                    "timeSpent": round(time_spent, 1),
                    "status": "wrong"
                })
        else:
            unattempted_count += 1
            status = "unattempted"

        question_evaluations.append({
            "id": q_id,
            "number": q["number"],
            "stem": q["stem"],
            "options": q["options"],
            "userAnswer": user_key if user_key else None,
            "correctAnswer": correct_key,
            "explanation": q.get("explanation"),
            "status": status,
            "isCorrect": is_correct,
            "isMarkedForReview": is_marked,
            "timeSpent": round(time_spent, 1)
        })

    # Calculate Topic-wise performance breakdown
    topic_stats = {}
    for q in questions:
        topic = q.get("topic") or "General Topics"
        if topic not in topic_stats:
            topic_stats[topic] = {"total": 0, "correct": 0, "attempted": 0}
        
        topic_stats[topic]["total"] += 1
        q_id = q["id"]
        user_key = payload.responses.get(q_id, "").lower().strip()
        correct_key = (q.get("correctAnswer") or "").lower().strip()
        
        if user_key:
            topic_stats[topic]["attempted"] += 1
            if user_key == correct_key:
                topic_stats[topic]["correct"] += 1

    topic_breakdown = []
    for t_name, stats in topic_stats.items():
        acc = round((stats["correct"] / stats["attempted"] * 100), 1) if stats["attempted"] > 0 else 0.0
        topic_breakdown.append({
            "topic": t_name,
            "total": stats["total"],
            "attempted": stats["attempted"],
            "correct": stats["correct"],
            "accuracy": acc
        })

    score = round(max(0.0, score), 2)
    accuracy = round((correct_count / attempted_count * 100), 2) if attempted_count > 0 else 0.0
    avg_time_per_q = round(payload.totalTimeTaken / total_questions, 1) if total_questions > 0 else 0.0

    submission_id = f"sub_{int(time.time())}"
    result_data = {
        "id": submission_id,
        "examId": payload.examId,
        "examTitle": exam.get("title", "Exam"),
        "candidateName": payload.candidateName or "Anonymous Candidate",
        "rollNumber": payload.rollNumber or "N/A",
        "timestamp": time.time(),
        "score": score,
        "maxScore": max_score,
        "totalQuestions": total_questions,
        "attempted": attempted_count,
        "correct": correct_count,
        "wrong": wrong_count,
        "unattempted": unattempted_count,
        "accuracy": accuracy,
        "totalTimeTaken": payload.totalTimeTaken,
        "avgTimePerQuestion": avg_time_per_q,
        "topicBreakdown": topic_breakdown,
        "errorQuestions": error_questions,
        "questionEvaluations": question_evaluations
    }

    # Save to submissions store
    submissions = load_json_data(SUBMISSIONS_FILE, [])
    submissions.insert(0, result_data)
    save_json_data(SUBMISSIONS_FILE, submissions)

    return result_data

@app.get("/api/submissions")
def get_all_submissions():
    return load_json_data(SUBMISSIONS_FILE, [])

@app.get("/api/analytics")
def get_admin_analytics():
    submissions = load_json_data(SUBMISSIONS_FILE, [])
    if not submissions:
        return {
            "totalSubmissions": 0,
            "avgScore": 0.0,
            "avgAccuracy": 0.0,
            "passRate": 0.0,
            "recentSubmissions": []
        }

    total_sub = len(submissions)
    avg_score = round(sum(s["score"] for s in submissions) / total_sub, 2)
    avg_accuracy = round(sum(s["accuracy"] for s in submissions) / total_sub, 2)
    passed_sub = sum(1 for s in submissions if s["accuracy"] >= 60.0)
    pass_rate = round((passed_sub / total_sub * 100), 1)

    return {
        "totalSubmissions": total_sub,
        "avgScore": avg_score,
        "avgAccuracy": avg_accuracy,
        "passRate": pass_rate,
        "recentSubmissions": submissions[:50]
    }

# Mount static directory for frontend
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

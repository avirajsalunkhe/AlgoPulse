import os
import json
import smtplib
import sys
import requests
import firebase_admin
import time
from datetime import datetime, timezone
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------- CONFIG ----------------
APP_ID = "leetcode-dsa-bot"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")

print(f"\n--- AlgoPulse Diagnostic Start [{datetime.now(timezone.utc)}] ---")

# ---------------- FIREBASE INIT ----------------
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")

if not firebase_admin._apps:
    if not service_account_json:
        print("❌ FIREBASE_SERVICE_ACCOUNT missing")
        sys.exit(1)

    cred = credentials.Certificate(json.loads(service_account_json))
    firebase_admin.initialize_app(cred)
    print("✅ Firebase initialized")

db = firestore.client()

# ---------------- UTIL ----------------
def clean_ai_response(text):
    if not text:
        return ""
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:])
    return text.strip()


# ---------------- AI CALL ----------------
def call_ai(prompt, is_json=True):

    # -------- GEMINI PRIMARY --------
    if GEMINI_API_KEY:
        print("🤖 Trying Gemini...")

        gemini_url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }

        if is_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        try:
            res = requests.post(gemini_url, json=payload, timeout=30)

            if res.status_code == 200:
                data = res.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                print("✅ Gemini success")
                return clean_ai_response(raw_text)

            print(f"⚠️ Gemini failed {res.status_code}: {res.text[:200]}")

        except Exception as e:
            print(f"⚠️ Gemini error: {e}")

    else:
        print("⚠️ GEMINI_API_KEY not set")

    # -------- GROQ FALLBACK --------
    if GROQ_API_KEY:
        print("🔄 Trying Groq...")

        groq_url = "https://api.groq.com/openai/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }

        if is_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            res = requests.post(groq_url, headers=headers, json=payload, timeout=30)

            if res.status_code == 200:
                raw_text = res.json()["choices"][0]["message"]["content"]
                print("✅ Groq success")
                return clean_ai_response(raw_text)

            print(f"❌ Groq failed {res.status_code}: {res.text[:200]}")

        except Exception as e:
            print(f"❌ Groq error: {e}")

    else:
        print("⚠️ GROQ_API_KEY not set")

    print("❌ All AI providers failed")
    return None


# ---------------- QUESTION BANK ----------------
def refill_bank(topic, difficulty):
    print(f"🧠 Generating new problems for {topic} ({difficulty})")

    prompt = (
        f"Generate exactly 5 unique LeetCode-style problems for '{topic}' "
        f"at '{difficulty}' difficulty. "
        "Return JSON array with fields: title, slug, description, constraints, example. "
        "Return ONLY raw JSON."
    )

    raw = call_ai(prompt, is_json=True)

    if not raw:
        return False

    try:
        problems = json.loads(raw)

        bank_ref = (
            db.collection("artifacts").document(APP_ID)
            .collection("public").document("data")
            .collection("question_bank")
        )

        for p in problems:
            bank_ref.add({
                "topic": topic,
                "difficulty": difficulty,
                "problem_data": json.dumps(p),
                "used": False,
                "createdAt": datetime.now(timezone.utc)
            })

        print(f"✅ Stored {len(problems)} problems")
        return True

    except Exception as e:
        print(f"❌ Failed storing problems: {e}")
        return False


def get_problem(topic, difficulty):

    bank_ref = (
        db.collection("artifacts").document(APP_ID)
        .collection("public").document("data")
        .collection("question_bank")
    )

    query = (
        bank_ref.where(filter=FieldFilter("topic", "==", topic))
        .where(filter=FieldFilter("difficulty", "==", difficulty))
        .where(filter=FieldFilter("used", "==", False))
        .limit(1)
        .stream()
    )

    for doc in query:
        bank_ref.document(doc.id).update({
            "used": True,
            "usedAt": datetime.now(timezone.utc)
        })
        return doc.to_dict()["problem_data"]

    print("⚠️ No unused problems. Generating...")
    if refill_bank(topic, difficulty):
        time.sleep(2)
        return get_problem(topic, difficulty)

    return None


# ---------------- EMAIL ----------------
def dispatch_email(to, subject, body):

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("❌ SMTP credentials missing")
        return False

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = f"AlgoPulse <{SENDER_EMAIL}>"
    msg["To"] = to
    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, to, msg.as_string())

        print(f"✅ Email sent to {to}")
        return True

    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False


# ---------------- MAIN ----------------
if __name__ == "__main__":

    print("🚀 MODE: MORNING")

    sub_ref = (
        db.collection("artifacts").document(APP_ID)
        .collection("public").document("data")
        .collection("subscribers")
    )

    subscribers = [doc.to_dict() for doc in sub_ref.stream() if doc.to_dict().get("status") == "active"]

    print(f"👥 Active subscribers: {len(subscribers)}")

    for user in subscribers:

        topic = user.get("topic", "BinarySearch")
        difficulty = user.get("difficulty", "Medium")

        problem_data = get_problem(topic, difficulty)

        if not problem_data:
            print("❌ Could not fetch problem")
            continue

        p = json.loads(problem_data)

        problem_url = f"https://leetcode.com/problems/{p['slug']}/"

        body = f"""
        <h2>{p['title']}</h2>
        <p><b>Difficulty:</b> {difficulty}</p>
        <p>{p['description']}</p>
        <br>
        <a href="{problem_url}">Solve on LeetCode</a>
        """

        dispatch_email(user["email"], f"{p['title']} ({difficulty})", body)

    print("\n--- AlgoPulse Finished ---")

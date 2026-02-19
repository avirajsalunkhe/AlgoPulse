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

# --- Configuration ---
APP_ID = "algopulse-v1" 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")

# Firebase Initialization
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
        print("✅ AlgoPulse Engine: Firebase initialized.")
    except Exception as e:
        print(f"❌ Failed to initialize Firebase: {e}")
        exit(1)

db = firestore.client()

def clean_ai_response(text):
    """Universal cleaner to remove markdown blocks from AI responses."""
    if not text:
        return ""
    if "```" in text:
        # Extract content between triple backticks
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            # Remove language identifier like 'json' or 'python'
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:])
        else:
            text = text.replace("```", "")
    return text.strip()

def call_ai(prompt, is_json=True):
    """Reliable AI call using Gemini 2.0 Flash."""
    url = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=){GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}], 
        "generationConfig": {"temperature": 0.2}
    }
    if is_json: 
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    try:
        res = requests.post(url, json=payload, timeout=45)
        if res.status_code == 200:
            raw_text = res.json()['candidates'][0]['content']['parts'][0]['text']
            return clean_ai_response(raw_text)
        else:
            print(f"❌ AI API Error: {res.status_code}")
    except Exception as e:
        print(f"❌ AI Exception: {e}")
    return None

def refill_bank(topic, difficulty):
    print(f"🧠 Generating problems for {topic} ({difficulty})...")
    prompt = (
        f"Generate exactly 5 unique LeetCode problems for '{topic}' at '{difficulty}' level. "
        "Return a JSON array of objects with: 'title', 'slug', 'description', 'constraints', 'example'. Output ONLY raw JSON."
    )
    raw = call_ai(prompt, is_json=True)
    if not raw: return False
    try:
        data = json.loads(raw)
        problems = data if isinstance(data, list) else data.get('problems', [])
        bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
        for p in problems:
            bank_ref.add({
                "topic": topic, "difficulty": difficulty, "problem_data": json.dumps(p),
                "used": False, "createdAt": datetime.now(timezone.utc)
            })
        print(f"✅ Refilled {len(problems)} problems.")
        return True
    except Exception as e:
        print(f"❌ JSON Parse Error in Refill: {e}")
        return False

def get_problem(topic, difficulty):
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    query = bank_ref.where(filter=FieldFilter("topic", "==", topic)) \
                    .where(filter=FieldFilter("difficulty", "==", difficulty)) \
                    .where(filter=FieldFilter("used", "==", False)) \
                    .limit(1).stream()
    
    for doc in query:
        bank_ref.document(doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return doc.to_dict()["problem_data"]
    
    if refill_bank(topic, difficulty):
        time.sleep(2)
        return get_problem(topic, difficulty)
    return None

def dispatch_email(to, subject, body):
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = f"AlgoPulse <{SENDER_EMAIL}>"
    msg['To'] = to
    msg.attach(MIMEText(body, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(SENDER_EMAIL, SENDER_PASSWORD)
            s.sendmail(SENDER_EMAIL, to, msg.as_string())
        return True
    except Exception as e:
        print(f"❌ SMTP Error for {to}: {e}")
        return False

if __name__ == "__main__":
    mode = "morning"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    
    if mode not in ["morning", "solution"]:
        hour = datetime.now(timezone.utc).hour
        mode = "morning" if hour < 12 else "solution"
        
    print(f"🚀 ENGINE START: Running in {mode.upper()} mode...")

    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    sub_list = [ {**doc.to_dict(), 'id': doc.id} for doc in subs ]
    
    print(f"👥 Found {len(sub_list)} active subscribers.")

    if mode == "morning":
        configs = set((u.get('topic', 'LogicBuilding'), u.get('difficulty', 'Medium')) for u in sub_list)
        packs = {f"{t}_{d}": get_problem(t, d) for t, d in configs}

        for u in sub_list:
            key = f"{u.get('topic', 'LogicBuilding')}_{u.get('difficulty', 'Medium')}"
            problem_data = packs.get(key)
            if problem_data:
                p = json.loads(problem_data)
                body = f"""
                <div style='background:#020617;color:white;padding:30px;font-family:sans-serif;border-radius:15px;'>
                    <h2 style='color:#3b82f6;'>Today's Challenge: {p['title']}</h2>
                    <p style='font-size:16px;line-height:1.6;'>{p['description']}</p>
                    <div style='background:#0f172a;padding:15px;border-radius:10px;margin:20px 0;'>
                        <b>Example:</b><br>{p.get('example', 'Check LeetCode for details')}
                    </div>
                    <a href='[https://leetcode.com/problems/](https://leetcode.com/problems/){p['slug']}/' style='display:inline-block;background:#3b82f6;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;'>Solve on LeetCode</a>
                </div>"""
                if dispatch_email(u['email'], f"🚀 Day {u.get('streak', 0)+1}: {p['title']}", body):
                    sub_ref.document(u['id']).update({
                        'streak': u.get('streak', 0) + 1,
                        'lastProblemData': problem_data,
                        'lastSentAt': datetime.now(timezone.utc)
                    })
                    print(f"✅ Morning mail sent to {u['email']}")
            else:
                print(f"⚠️ No problem found for {key}")

    elif mode == "solution":
        for u in sub_list:
            problem_data = u.get('lastProblemData')
            if not problem_data:
                print(f"⚠️ Skipping {u['email']}: No problem record.")
                continue

            p = json.loads(problem_data)
            lang = u.get('language', 'Python')
            print(f"🛠️ Solving '{p['title']}' in {lang}...")
            
            prompt = f"Provide a clean, efficient {lang} code solution for the LeetCode problem: '{p['title']}'. Output ONLY the code."
            solution = call_ai(prompt, is_json=False)
            
            if solution:
                body = f"""
                <div style='background:#020617;color:white;padding:30px;font-family:sans-serif;'>
                    <h2 style='color:#10b981;'>Solution Recap: {p['title']}</h2>
                    <p>Language: <b>{lang}</b></p>
                    <div style='background:#0f172a;padding:20px;border-radius:12px;border:1px solid #1e293b;'>
                        <pre style='color:#34d399;font-family:monospace;margin:0;'>{solution}</pre>
                    </div>
                </div>"""
                if dispatch_email(u['email'], f"✅ Solution: {p['title']}", body):
                    print(f"✅ Evening solution sent to {u['email']}")

    print(f"🏁 ENGINE FINISHED.")

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

def call_ai(prompt, is_json=True):
    """Reliable AI call using Gemini 2.0 Flash."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}], 
        "generationConfig": {"temperature": 0.7}
    }
    if is_json: 
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"❌ AI Error: {e}")
    return None

def refill_bank(topic, difficulty):
    """Generates a fresh batch of problems if the bank is empty."""
    print(f"🧠 Generating fresh problems for {topic} ({difficulty})...")
    prompt = (
        f"Generate exactly 5 unique LeetCode problems for '{topic}' at '{difficulty}' level. "
        "Return a JSON array of objects with: 'title', 'slug', 'description', 'constraints', 'example'. Output ONLY raw JSON."
    )
    raw = call_ai(prompt, is_json=True)
    if not raw: return False
    try:
        clean_json = raw.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            
        data = json.loads(clean_json)
        problems = data if isinstance(data, list) else data.get('problems', [])
        
        bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
        for p in problems:
            bank_ref.add({
                "topic": topic, "difficulty": difficulty, "problem_data": json.dumps(p),
                "used": False, "createdAt": datetime.now(timezone.utc)
            })
        print(f"✅ Refilled {len(problems)} problems for {difficulty} {topic}")
        return True
    except Exception as e:
        print(f"❌ Refill Parse Error: {e}")
        return False

def get_problem(topic, difficulty):
    """Fetches one unused problem from the bank."""
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    query = bank_ref.where(filter=FieldFilter("topic", "==", topic)) \
                    .where(filter=FieldFilter("difficulty", "==", difficulty)) \
                    .where(filter=FieldFilter("used", "==", False)) \
                    .limit(1).stream()
    
    for doc in query:
        bank_ref.document(doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return doc.to_dict()["problem_data"]
    
    if refill_bank(topic, difficulty):
        time.sleep(2) # Firestore consistency delay
        return get_problem(topic, difficulty)
    return None

def dispatch_email(to, subject, body):
    """Sends a formatted HTML email."""
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

def send_morning_challenge(user, problem_json):
    """Template for the 7 AM morning challenge."""
    p = json.loads(problem_json)
    streak = user.get('streak', 0) + 1
    lang = user.get('language', 'Python')
    color = {"Easy": "#10b981", "Medium": "#f59e0b", "Hard": "#ef4444"}.get(user['difficulty'], "#3b82f6")

    body = f"""
    <div style="font-family: sans-serif; background:#020617; padding:40px; color: #f8fafc;">
        <div style="max-width:600px; margin:auto; background:#0f172a; border-radius:24px; padding:40px; border:1px solid #1e293b;">
            <div style="text-align:center; margin-bottom:32px;">
                <div style="display:inline-block; background:#1e1b4b; color:#818cf8; padding:6px 16px; border-radius:100px; font-weight:800; font-size:11px; letter-spacing:1px; margin-bottom:12px;">DAY {streak} • {lang.upper()}</div>
                <h1 style="color:white; margin:0; font-size:26px;">{p['title']}</h1>
            </div>
            <div style="background:#020617; padding:24px; border-radius:16px; border-top:4px solid {color};">
                <div style="color:{color}; font-weight:bold; font-size:12px; margin-bottom:8px;">{user['difficulty'].upper()} CHALLENGE</div>
                <p style="color:#94a3b8; font-size:15px; line-height:1.6; margin-bottom:20px;">{p['description']}</p>
                <div style="background:#0f172a; padding:15px; border-radius:10px; font-family: monospace; font-size:13px; color:#e2e8f0; border:1px solid #1e293b;">
                    <b style="color:#64748b; font-size:10px;">EXAMPLE</b><br>{p.get('example', 'Check LeetCode for details')}
                </div>
            </div>
            <div style="margin-top:32px; text-align:center;">
                <a href="[https://leetcode.com/problems/](https://leetcode.com/problems/){p['slug']}/" style="display:inline-block; background:#3b82f6; color:white; padding:16px 48px; border-radius:12px; text-decoration:none; font-weight:bold; font-size:14px;">Solve Now</a>
            </div>
        </div>
    </div>"""
    return dispatch_email(user['email'], f"🚀 Day {streak}: {p['title']} ({user['difficulty']})", body)

def send_evening_solution(user, problem_json, solution_code):
    """Template for the 8 PM solution recap."""
    p = json.loads(problem_json)
    body = f"""
    <div style="font-family: sans-serif; background:#020617; padding:40px; color: #f8fafc;">
        <div style="max-width:600px; margin:auto; background:#0f172a; border-radius:24px; padding:40px; border:1px solid #1e293b;">
            <h1 style="color:white; margin-bottom:12px; font-size:24px;">Solution: {p['title']}</h1>
            <p style="color:#94a3b8; font-size:14px; margin-bottom:24px;">Here is the optimal solution in <b>{user['language']}</b> for this morning's challenge.</p>
            <div style="background:#020617; padding:24px; border-radius:16px; border:1px solid #334155;">
                <pre style="color:#34d399; font-family: monospace; font-size:13px; margin:0; white-space: pre-wrap;">{solution_code}</pre>
            </div>
        </div>
    </div>"""
    return dispatch_email(user['email'], f"✅ Solution: {p['title']}", body)

if __name__ == "__main__":
    # Determine mode from command line args
    mode = "morning"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
        
    print(f"🚀 AlgoPulse Engine: Running in {mode.upper()} mode...")

    # Fetch active subscribers
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    sub_list = [ {**doc.to_dict(), 'id': doc.id} for doc in subs ]
    
    print(f"👥 Found {len(sub_list)} active subscribers.")

    if mode == "morning":
        # Process unique configurations to save AI calls
        configs = set((u.get('topic', 'LogicBuilding'), u.get('difficulty', 'Medium')) for u in sub_list)
        packs = {f"{t}_{d}": get_problem(t, d) for t, d in configs}

        for u in sub_list:
            key = f"{u.get('topic', 'LogicBuilding')}_{u.get('difficulty', 'Medium')}"
            problem_data = packs.get(key)
            if problem_data and send_morning_challenge(u, problem_data):
                # Update streak and save the problem for the evening solution run
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastProblemData': problem_data,
                    'lastDelivery': datetime.now(timezone.utc)
                })
                print(f"✅ Morning challenge sent to {u['email']}")

    elif mode == "solution":
        for u in sub_list:
            problem_data = u.get('lastProblemData')
            if problem_data:
                p_title = json.loads(problem_data)['title']
                print(f"🛠️ Generating solution for {u['email']} ({p_title})...")
                prompt = (
                    f"Provide ONLY the clean, optimized code solution for the LeetCode problem '{p_title}' "
                    f"in {u['language']}. No explanation or markdown blocks. Just the raw code."
                )
                solution = call_ai(prompt, is_json=False)
                if solution and send_evening_solution(u, problem_data, solution.strip()):
                    print(f"✅ Evening solution sent to {u['email']}")
            else:
                print(f"⚠️ No morning problem data found for {u['email']}")

    print(f"🏁 Execution finished.")

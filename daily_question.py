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
# The APP_ID scopes your data in Firestore to prevent conflicts with other projects
APP_ID = "leetcode-dsa-bot"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
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
        print(f"❌ Initialization Error: {e}")
        exit(1)

db = firestore.client()

def call_ai(prompt, is_json=True):
    """Robust AI caller with Gemini primary and Groq fallback."""
    # 1. Try Gemini 2.0 Flash
    if GEMINI_API_KEY:
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
        except Exception:
            pass

    # 2. Try Groq (Llama 3.3) Fallback
    if GROQ_API_KEY:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "system", "content": "You are a DSA expert. Output raw code or JSON only."}, 
                         {"role": "user", "content": prompt}]
        }
        if is_json:
            payload["response_format"] = {"type": "json_object"}
            
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                return res.json()['choices'][0]['message']['content']
        except Exception:
            pass
            
    return None

def refill_question_bank(topic, difficulty):
    """Populates the database with fresh LeetCode problems if the bank is empty."""
    print(f"🧠 Generating new {difficulty} problems for {topic}...")
    prompt = (
        f"Generate exactly 10 unique LeetCode-style problems for the track '{topic}' at '{difficulty}' difficulty. "
        "Return a JSON array of objects. Each object must have: 'title', 'slug' (url-friendly name), "
        "'description', 'constraints', and 'example'. Output ONLY raw JSON."
    )
    
    raw_response = call_ai(prompt, is_json=True)
    if not raw_response: return False

    try:
        clean_json = raw_response.strip().strip('```json').strip('```')
        data = json.loads(clean_json)
        problems = data if isinstance(data, list) else data.get('problems', [])
        
        bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
        for p in problems:
            bank_ref.add({
                "topic": topic,
                "difficulty": difficulty,
                "problem_data": json.dumps(p),
                "used": False,
                "createdAt": datetime.now(timezone.utc)
            })
        return True
    except Exception as e:
        print(f"⚠️ Parsing Error: {e}")
        return False

def get_morning_problem(topic, difficulty):
    """Retrieves an unused problem from the bank, refilling if necessary."""
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    query = bank_ref.where(filter=FieldFilter("topic", "==", topic)) \
                    .where(filter=FieldFilter("difficulty", "==", difficulty)) \
                    .where(filter=FieldFilter("used", "==", False)) \
                    .limit(1).stream()
    
    found_doc = None
    for doc in query:
        found_doc = doc
        break
        
    if found_doc:
        bank_ref.document(found_doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return found_doc.id, found_doc.to_dict()["problem_data"]
    
    if refill_question_bank(topic, difficulty):
        time.sleep(2) # Buffer for consistency
        return get_morning_problem(topic, difficulty) 
        
    return None, None

def send_morning_challenge(user, problem_json):
    p = json.loads(problem_json)
    streak = user.get('streak', 0) + 1
    lang = user.get('language', 'Python')
    color = {"Easy": "#10b981", "Medium": "#f59e0b", "Hard": "#ef4444"}.get(user['difficulty'], "#3b82f6")
    
    body = f"""
    <div style="font-family: 'Inter', sans-serif; background:#020617; padding:40px; color: #f8fafc;">
        <div style="max-width:600px; margin:auto; background:#0f172a; border-radius:24px; padding:40px; border:1px solid #1e293b; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);">
            <div style="text-align:center; margin-bottom:32px;">
                <div style="display:inline-block; background:#1e1b4b; color:#818cf8; padding:6px 16px; border-radius:100px; font-weight:800; font-size:11px; letter-spacing:1px; margin-bottom:12px;">DAY {streak} • {lang.upper()}</div>
                <h1 style="color:white; margin:0; font-size:26px; font-weight:900;">{p['title']}</h1>
            </div>
            <div style="background:#020617; padding:24px; border-radius:16px; border-top:4px solid {color};">
                <div style="color:{color}; font-weight:bold; font-size:12px; margin-bottom:8px;">{user['difficulty'].upper()} CHALLENGE</div>
                <p style="color:#94a3b8; font-size:15px; line-height:1.6; margin-bottom:20px;">{p['description']}</p>
                <div style="background:#0f172a; padding:15px; border-radius:10px; font-family: 'JetBrains Mono', monospace; font-size:13px; color:#e2e8f0; border:1px solid #1e293b;">
                    <b style="color:#64748b; font-size:10px;">CONSTRAINTS & EXAMPLES</b><br>{p.get('example', 'See LeetCode for full constraints.')}
                </div>
            </div>
            <div style="margin-top:32px; text-align:center;">
                <a href="https://leetcode.com/problems/{p['slug']}/" style="display:inline-block; background:#3b82f6; color:white; padding:16px 48px; border-radius:12px; text-decoration:none; font-weight:bold; font-size:14px; box-shadow:0 10px 15px -3px rgba(59, 130, 246, 0.3);">Solve Problem</a>
            </div>
        </div>
        <p style="text-align:center; font-size:10px; color:#475569; margin-top:24px; text-transform:uppercase; letter-spacing:1px;">AlgoPulse • Solution arrives at 8:00 PM IST</p>
    </div>"""
    return dispatch_email(user['email'], f"🚀 Day {streak}: {p['title']} ({user['difficulty']})", body)

def send_evening_solution(user, problem_data, solution_code):
    p = json.loads(problem_data)
    lang = user.get('language', 'Python')
    
    body = f"""
    <div style="font-family: 'Inter', sans-serif; background:#020617; padding:40px; color: #f8fafc;">
        <div style="max-width:600px; margin:auto; background:#0f172a; border-radius:24px; padding:40px; border:1px solid #1e293b;">
            <div style="margin-bottom:24px;">
                <div style="color:#10b981; font-weight:bold; font-size:11px; letter-spacing:1px; margin-bottom:4px;">EVENING RECAP</div>
                <h1 style="color:white; margin:0; font-size:24px; font-weight:800;">Solution: {p['title']}</h1>
            </div>
            <p style="color:#94a3b8; font-size:14px; margin-bottom:24px; line-height:1.5;">Here is an optimized, well-commented solution in <b>{lang}</b> for this morning's challenge.</p>
            <div style="background:#020617; padding:24px; border-radius:16px; border:1px solid #334155; position:relative;">
                <pre style="color:#34d399; font-family: 'JetBrains Mono', monospace; font-size:13px; margin:0; white-space: pre-wrap; word-wrap: break-word; line-height:1.6;">{solution_code}</pre>
            </div>
            <div style="margin-top:20px; color:#64748b; font-size:12px; font-style:italic; text-align:center;">Copy and paste the code above into LeetCode to verify your solution.</div>
        </div>
    </div>"""
    return dispatch_email(user['email'], f"✅ Solution: {p['title']} ({lang})", body)

def dispatch_email(to, subject, body):
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = f"AlgoPulse Daily <{SENDER_EMAIL}>"
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
    # Determine mode: morning (7AM) or solution (8PM)
    mode = "morning"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    
    print(f"🚀 AlgoPulse Engine: Running in {mode.upper()} mode...")
    
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    sub_list = [ {**doc.to_dict(), 'id': doc.id} for doc in subs ]
    
    if mode == "morning":
        # Process challenges
        configs = set((u.get('topic', 'LogicBuilding'), u.get('difficulty', 'Medium')) for u in sub_list)
        packs = { f"{t}_{d}": get_morning_problem(t, d) for t, d in configs }
        
        for u in sub_list:
            key = f"{u.get('topic', 'LogicBuilding')}_{u.get('difficulty', 'Medium')}"
            p_id, p_data = packs.get(key, (None, None))
            if p_id and send_morning_challenge(u, p_data):
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastProblemId': p_id,
                    'lastProblemData': p_data,
                    'lastSentAt': datetime.now(timezone.utc)
                })
    
    elif mode == "solution":
        # Group users by problem and language to batch AI solution generation
        groups = {}
        for u in sub_list:
            if u.get('lastProblemId') and u.get('lastProblemData'):
                key = (u['lastProblemId'], u['language'])
                groups.setdefault(key, []).append(u)
        
        for (p_id, lang), users in groups.items():
            p_data = users[0]['lastProblemData']
            p_title = json.loads(p_data)['title']
            
            prompt = (
                f"Provide ONLY the clean, optimized code for the LeetCode problem '{p_title}' in {lang}. "
                "Include brief comments explaining the logic. Do NOT use markdown code blocks or triple backticks. "
                "Output ONLY the raw code."
            )
            
            solution = call_ai(prompt, is_json=False)
            if solution:
                for u in users:
                    send_evening_solution(u, p_data, solution.strip())

    print(f"✅ AlgoPulse Cycle Complete.")

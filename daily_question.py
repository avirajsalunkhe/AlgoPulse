import os
import json
import smtplib
import requests
import firebase_admin
import time
import sys
import re
from datetime import datetime, timezone
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Configuration ---
APP_ID = "leetcode-dsa-bot"
DASHBOARD_URL = "https://avirajsalunkhe.github.io/algo-pulse" # Update as needed
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")

# --- Firebase Initialization ---
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized.")
    except Exception as e:
        print(f"❌ Firebase Init Failed: {e}")
        sys.exit(1)

db = firestore.client()

# --- Utility ---

def clean_json_string(raw_str):
    """Robustly extracts JSON from AI responses that might contain markdown or fluff."""
    if not raw_str:
        return None
    # Remove markdown code blocks if they exist
    clean = re.sub(r'```json\s*|\s*```', '', raw_str).strip()
    # Try to find the first '{' or '[' and the last '}' or ']'
    start_idx = max(clean.find('{'), clean.find('['))
    end_idx = max(clean.rfind('}'), clean.rfind(']'))
    
    if start_idx != -1 and end_idx != -1:
        return clean[start_idx:end_idx+1]
    return clean

# --- AI Interaction Layer ---

def call_ai_with_fallback(prompt, is_json=True):
    """Calls Gemini with exponential backoff, falling back to Groq on failure."""
    
    # Strategy 1: Gemini (Primary)
    if GEMINI_API_KEY:
        print("🤖 Attempting Gemini...")
        for delay in [1, 2, 4, 8, 16]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7}
                }
                if is_json:
                    payload["generationConfig"]["responseMimeType"] = "application/json"
                
                res = requests.post(url, json=payload, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    return data['candidates'][0]['content']['parts'][0]['text']
                elif res.status_code == 429:
                    print(f"⚠️ Gemini Rate Limited. Retrying in {delay}s...")
                else:
                    print(f"⚠️ Gemini API Error {res.status_code}: {res.text[:100]}")
                    break
            except Exception as e:
                print(f"⚠️ Gemini Connection Error: {e}")
            time.sleep(delay)

    # Strategy 2: Groq (Fallback)
    if GROQ_API_KEY:
        print("🔄 Attempting Groq Fallback...")
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "system", "content": "You are a DSA expert. Return valid JSON only."}, {"role": "user", "content": prompt}],
                "temperature": 0.5
            }
            if is_json:
                payload["response_format"] = {"type": "json_object"}
            
            res = requests.post(url, json=payload, headers=headers, timeout=30)
            if res.status_code == 200:
                return res.json()['choices'][0]['message']['content']
            else:
                print(f"❌ Groq API Error {res.status_code}: {res.text[:100]}")
        except Exception as e:
            print(f"⚠️ Groq Fallback Error: {e}")

    return None

# --- Data Management ---

def refill_question_bank(topic, difficulty):
    """Generates 5 new problems for the bank when empty."""
    print(f"🧠 Refilling bank for {topic} - {difficulty}...")
    prompt = (
        f"Generate 5 unique DSA problems for topic '{topic}' at '{difficulty}' difficulty level. "
        "Return a JSON object with a key 'problems' containing an array of objects. "
        "Each object MUST have: {title, description, constraints, examples, approach, complexity, code_snippet}. "
        "Ensure descriptions are concise and formatted nicely for an email."
    )
    
    raw_res = call_ai_with_fallback(prompt)
    if not raw_res: 
        print("❌ AI failed to provide a response for bank refill.")
        return False

    try:
        clean_json = clean_json_string(raw_res)
        data = json.loads(clean_json)
        problems = data.get('problems', [])
        
        if not problems:
            print("⚠️ AI response was empty or incorrectly structured.")
            return False

        bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
        for p in problems:
            bank_ref.add({
                "topic": topic,
                "difficulty": difficulty,
                "problem_data": json.dumps(p),
                "used": False,
                "createdAt": datetime.now(timezone.utc)
            })
        print(f"✅ Successfully added {len(problems)} problems to the bank.")
        return True
    except Exception as e:
        print(f"❌ Parsing Error during refill: {e}")
        return False

def get_problem(topic, difficulty):
    """Fetches an unused problem or triggers a refill."""
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    query = bank_ref.where(filter=FieldFilter("topic", "==", topic))\
                    .where(filter=FieldFilter("difficulty", "==", difficulty))\
                    .where(filter=FieldFilter("used", "==", False)).limit(1).stream()
    
    for doc in query:
        bank_ref.document(doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return doc.to_dict()["problem_data"]

    # If no problem found, try refilling
    if refill_question_bank(topic, difficulty):
        time.sleep(2) # Firestore consistency delay
        return get_problem(topic, difficulty)
    return None

# --- Email Templates ---

def send_morning_challenge(user, problem_json):
    p = json.loads(problem_json)
    email = user['email']
    
    body = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; padding: 40px 10px;">
        <div style="max-width: 600px; margin: auto; background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;">
            <div style="background: #0f172a; padding: 30px; text-align: center;">
                <h1 style="color: #38bdf8; margin: 0; font-size: 24px; letter-spacing: 1px;">ALGOPULSE MORNING</h1>
                <p style="color: #94a3b8; margin-top: 5px;">Level Up Your DSA Daily</p>
            </div>
            <div style="padding: 40px;">
                <div style="display: inline-block; background: #f1f5f9; color: #475569; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; margin-bottom: 20px;">
                    {user.get('difficulty', 'Medium').upper()} • {user.get('topic', 'DSA')}
                </div>
                <h2 style="color: #1e293b; margin: 0 0 15px 0; font-size: 22px;">{p['title']}</h2>
                <div style="color: #475569; line-height: 1.7; font-size: 15px; margin-bottom: 25px;">
                    {p['description']}
                </div>
                <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 30px;">
                    <h3 style="margin: 0 0 10px 0; font-size: 14px; color: #64748b;">CONSTRAINTS</h3>
                    <code style="color: #e11d48; font-size: 13px;">{p.get('constraints', 'N/A')}</code>
                </div>
                <a href="{DASHBOARD_URL}" style="display: block; text-align: center; background: #2563eb; color: white; padding: 15px; border-radius: 10px; text-decoration: none; font-weight: bold; font-size: 16px;">Solve Problem</a>
            </div>
        </div>
    </div>
    """
    return dispatch_email(email, f"☀️ Morning Challenge: {p['title']}", body)

def send_solution_dispatch(user, problem_json):
    p = json.loads(problem_json)
    email = user['email']
    
    body = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0fdf4; padding: 40px 10px;">
        <div style="max-width: 600px; margin: auto; background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #dcfce7;">
            <div style="background: #166534; padding: 30px; text-align: center;">
                <h1 style="color: #86efac; margin: 0; font-size: 24px;">SOLUTION ANALYSIS</h1>
                <p style="color: #bbf7d0; margin-top: 5px;">Day {user.get('streak', 0)} Complete</p>
            </div>
            <div style="padding: 40px;">
                <h2 style="color: #14532d; margin-top: 0;">Optimal Approach</h2>
                <p style="color: #374151; line-height: 1.6;">{p.get('approach', 'Check the dashboard for detailed logic.')}</p>
                
                <div style="background: #1e293b; color: #e2e8f0; padding: 20px; border-radius: 8px; font-family: monospace; font-size: 13px; margin: 20px 0; overflow-x: auto;">
                    <pre style="margin: 0;">{p.get('code_snippet', '// Solution available in dashboard')}</pre>
                </div>
                
                <div style="display: flex; gap: 10px; margin-top: 20px;">
                    <div style="flex: 1; background: #f0fdf4; padding: 15px; border-radius: 8px; border: 1px solid #bbf7d0;">
                        <div style="font-size: 11px; color: #166534; font-weight: bold;">TIME</div>
                        <div style="font-weight: bold; color: #14532d;">{p.get('complexity', {}).get('time', 'O(N)') if isinstance(p.get('complexity'), dict) else 'O(N)'}</div>
                    </div>
                    <div style="flex: 1; background: #f0fdf4; padding: 15px; border-radius: 8px; border: 1px solid #bbf7d0;">
                        <div style="font-size: 11px; color: #166534; font-weight: bold;">SPACE</div>
                        <div style="font-weight: bold; color: #14532d;">{p.get('complexity', {}).get('space', 'O(1)') if isinstance(p.get('complexity'), dict) else 'O(1)'}</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """
    return dispatch_email(email, f"✅ Solution: {p['title']}", body)

def dispatch_email(to, subject, html_body):
    if not SENDER_EMAIL or not SENDER_PASSWORD: 
        print(f"❌ Missing SMTP credentials for {to}")
        return False
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = f"AlgoPulse <{SENDER_EMAIL}>"
    msg['To'] = to
    msg.attach(MIMEText(html_body, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(SENDER_EMAIL, SENDER_PASSWORD)
            s.sendmail(SENDER_EMAIL, to, msg.as_string())
        print(f"📧 Email sent to {to}")
        return True
    except Exception as e:
        print(f"❌ SMTP Error for {to}: {e}")
        return False

# --- Main Execution Logic ---

def run_dispatch(mode="morning"):
    # Clean up mode just in case it's passed as '--MODE' or something similar
    mode = mode.replace('-', '').lower()
    if mode not in ["morning", "solution"]:
        # Fallback to time-based detection if mode is invalid
        hour = datetime.now().hour
        mode = "morning" if 4 <= hour < 14 else "solution"

    print(f"🚀 Running {mode.upper()} Dispatch...")
    
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    
    active_subs = []
    for doc in subs:
        data = doc.to_dict()
        data['id'] = doc.id
        active_subs.append(data)
    
    print(f"👥 Subscribers found: {len(active_subs)}")
    
    problem_cache = {}
    success_count = 0

    for user in active_subs:
        topic = user.get('topic', 'Arrays')
        difficulty = user.get('difficulty', 'Medium')
        cache_key = f"{topic}_{difficulty}"

        if mode == "solution":
            problem_json = user.get('last_problem_data')
            if not problem_json: 
                print(f"⚠️ No morning problem recorded for {user['email']}. Skipping solution.")
                continue
            
            if send_solution_dispatch(user, problem_json):
                success_count += 1
        else:
            # Morning Mode: Get new problem
            if cache_key not in problem_cache:
                problem_cache[cache_key] = get_problem(topic, difficulty)
            
            problem_json = problem_cache[cache_key]
            if not problem_json: 
                print(f"❌ Failed to get problem for {topic}/{difficulty}")
                continue

            if send_morning_challenge(user, problem_json):
                sub_ref.document(user['id']).update({
                    "last_problem_data": problem_json,
                    "last_sent": datetime.now(timezone.utc),
                    "streak": user.get('streak', 0) + 1
                })
                success_count += 1

    print(f"🏁 Finished. Successful sends: {success_count}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Check if the argument is a mode or a placeholder
        cmd_arg = sys.argv[1].lower()
        if cmd_arg.startswith('--'):
            # Detect based on time if placeholder '--MODE' is passed
            hour = datetime.now().hour
            mode = "morning" if 4 <= hour < 14 else "solution"
        else:
            mode = cmd_arg
    else:
        # Default time-based detection
        hour = datetime.now().hour
        mode = "morning" if 4 <= hour < 14 else "solution"
    
    run_dispatch(mode)

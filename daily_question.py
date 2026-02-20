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
DASHBOARD_URL = "https://avirajsalunkhe.github.io/algo-pulse/" 

# Mapping secrets based on provided screenshot
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") 
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")

# --- Firebase Initialization ---
if not firebase_admin._apps:
    try:
        if not FIREBASE_SERVICE_ACCOUNT:
            print("❌ FIREBASE_SERVICE_ACCOUNT secret is missing.")
            sys.exit(1)
        
        cred_dict = json.loads(FIREBASE_SERVICE_ACCOUNT)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized successfully.")
    except Exception as e:
        print(f"❌ Failed to initialize Firebase: {e}")
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

# --- AI Providers ---

def fetch_from_gemini(prompt):
    """Attempt to get content from Gemini API with fallback for 404s and 429s."""
    if not GEMINI_API_KEY: 
        print("⚠️ GEMINI_API_KEY missing.")
        return None
    
    strategies = [
        ("v1beta", "gemini-2.0-flash", True),
        ("v1", "gemini-1.5-flash", False),
        ("v1beta", "gemini-1.5-flash", True),
    ]
    
    for api_version, model, use_json in strategies:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8}
        }
        if use_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                print(f"✅ Gemini {model} success.")
                return data['candidates'][0]['content']['parts'][0]['text']
            elif res.status_code == 429:
                print(f"    ⚠️ Gemini {model} rate limited (429).")
            elif res.status_code == 404:
                print(f"    ⚠️ Gemini {model} not found (404).")
            else:
                print(f"    ⚠️ Gemini {model} error: {res.status_code}")
        except Exception as e:
            print(f"    ⚠️ Gemini connection error: {e}")
            
        time.sleep(2)
    return None

def fetch_from_groq(prompt):
    """Attempt to get content from Groq API (Llama 3). Highly reliable free alternative."""
    if not GROQ_API_KEY: 
        print("    ℹ️ Groq API Key not found in environment. Ensure it is mapped in daily_automation.yml.")
        return None
    
    print(f"    🚀 Attempting Groq Fallback (Llama-3)...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a DSA expert. Output ONLY valid raw JSON."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        if res.status_code == 200:
            data = res.json()
            print("✅ Groq success.")
            return data['choices'][0]['message']['content']
        else:
            print(f"    ⚠️ Groq failed with status {res.status_code}: {res.text}")
    except Exception as e:
        print(f"    ⚠️ Groq connection error: {e}")
    return None

# --- Data Management ---

def refill_question_bank(topic, difficulty):
    """Generates 5 new problems using AI with fallback and stores them in the bank."""
    print(f"🧠 Bank empty for {topic} ({difficulty}). Refilling...")
    
    # Updated prompt to include 'slug' for LeetCode URL construction
    prompt = (
        f"Generate exactly 5 unique DSA problems for topic '{topic}' at '{difficulty}' level. "
        "Return a JSON object with a key 'problems' containing an array of 5 objects. "
        "Each object MUST have: {title, slug, description, constraints, examples, approach, complexity, code_snippet}. "
        "The 'slug' should be a valid URL-friendly version of the title (e.g., 'two-sum' for 'Two Sum'). "
        "Complexity should be a map: {time, space}."
    )

    # Try Gemini First
    raw_response = fetch_from_gemini(prompt)
    
    # Fallback to Groq if Gemini fails
    if not raw_response:
        raw_response = fetch_from_groq(prompt)
        
    if not raw_response:
        print("❌ All AI providers failed.")
        return False

    try:
        # Clean JSON from markdown blocks as per requested logic
        clean_json = raw_response.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()
        
        data = json.loads(clean_json)
        problems = data.get('problems', []) if isinstance(data, dict) else data
        
        if not problems:
            print("    ⚠️ No problems found in AI response.")
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
        print(f"✅ Successfully added {len(problems)} new problems to bank.")
        return True
    except Exception as e:
        print(f"    ⚠️ Error parsing AI response: {e}")
        return False

def get_problem(topic, difficulty):
    """Retrieves an unused problem from the bank."""
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    
    query = bank_ref.where(filter=FieldFilter("topic", "==", topic))\
                    .where(filter=FieldFilter("difficulty", "==", difficulty))\
                    .where(filter=FieldFilter("used", "==", False))\
                    .limit(1).stream()
    
    found_doc = None
    for doc in query:
        found_doc = doc
        break
        
    if found_doc:
        bank_ref.document(found_doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return found_doc.to_dict()["problem_data"]
    
    if refill_question_bank(topic, difficulty):
        time.sleep(2) 
        return get_problem(topic, difficulty) 
        
    return None

# --- Email Templates ---

def send_morning_challenge(user, problem_json):
    p = json.loads(problem_json)
    email = user['email']
    streak = user.get('streak', 0) + 1
    
    # Construct LeetCode URL
    # If the slug is present, use it; otherwise fallback to a generic search
    slug = p.get('slug', '')
    leetcode_url = f"https://leetcode.com/problems/{slug}/" if slug else "https://leetcode.com/problemset/all/"
    
    body = f"""
    <div style="font-family: 'Segoe UI', sans-serif; background-color: #f8fafc; padding: 40px 10px;">
        <div style="max-width: 600px; margin: auto; background: white; border-radius: 20px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;">
            <div style="background: #0f172a; padding: 30px; text-align: center;">
                <h1 style="color: #38bdf8; margin: 0; font-size: 24px;">ALGOPULSE MORNING</h1>
                <div style="display:inline-block; margin-top:10px; background:#1e293b; color:#38bdf8; padding:4px 12px; border-radius:12px; font-weight:bold; font-size:12px;">🔥 {streak} DAY STREAK</div>
            </div>
            <div style="padding: 40px;">
                <div style="display: inline-block; background: #f1f5f9; color: #475569; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; margin-bottom: 20px;">
                    {user.get('difficulty', 'Medium').upper()} • {user.get('topic', 'DSA')}
                </div>
                <h2 style="color: #1e293b; margin: 0 0 15px 0;">{p['title']}</h2>
                <div style="color: #475569; line-height: 1.7; font-size: 15px; margin-bottom: 25px;">{p['description']}</div>
                
                <div style="margin-top: 30px; text-align: center;">
                    <a href="{leetcode_url}" style="display: inline-block; background: #ffa116; color: white; padding: 12px 25px; border-radius: 10px; text-decoration: none; font-weight: bold; margin-right: 10px;">Solve on LeetCode</a>
                    <a href="{DASHBOARD_URL}" style="display: inline-block; background: #2563eb; color: white; padding: 12px 25px; border-radius: 10px; text-decoration: none; font-weight: bold;">View Dashboard</a>
                </div>
            </div>
        </div>
    </div>
    """
    return dispatch_email(email, f"☀️ Morning Challenge: {p['title']}", body)

def send_solution_dispatch(user, problem_json):
    p = json.loads(problem_json)
    email = user['email']
    
    body = f"""
    <div style="font-family: 'Segoe UI', sans-serif; background-color: #f0fdf4; padding: 40px 10px;">
        <div style="max-width: 600px; margin: auto; background: white; border-radius: 20px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);">
            <div style="background: #166534; padding: 30px; text-align: center;">
                <h1 style="color: #86efac; margin: 0; font-size: 24px;">SOLUTION ANALYSIS</h1>
            </div>
            <div style="padding: 40px;">
                <h2 style="color: #14532d;">Approach & Logic</h2>
                <p style="color: #374151; line-height: 1.6;">{p.get('approach', 'Check dashboard for details.')}</p>
                <div style="background: #1e293b; color: #e2e8f0; padding: 20px; border-radius: 8px; font-family: monospace; font-size: 13px; margin: 20px 0;">
                    <pre style="margin: 0;">{p.get('code_snippet', '// Solution available in dashboard')}</pre>
                </div>
            </div>
        </div>
    </div>
    """
    return dispatch_email(email, f"✅ Solution: {p['title']}", body)

def dispatch_email(to, subject, html_body):
    if not SENDER_EMAIL or not SENDER_PASSWORD: 
        print(f"❌ Missing email credentials for {to}")
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
        print(f"📧 Sent to {to}")
        return True
    except Exception as e:
        print(f"❌ SMTP Error: {e}")
        return False

# --- Main Dispatcher ---

def run_dispatch(mode="morning"):
    print(f"🚀 Dispatching {mode.upper()} at {datetime.now(timezone.utc)}")
    
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    
    active_subs = []
    for doc in subs:
        data = doc.to_dict()
        data['id'] = doc.id
        active_subs.append(data)
    
    if not active_subs:
        print("ℹ️ No active subscribers found.")
        return

    print(f"👥 Processing {len(active_subs)} users...")
    
    problem_cache = {}
    success_count = 0

    for user in active_subs:
        topic = user.get('topic', 'Arrays')
        difficulty = user.get('difficulty', 'Medium')
        cache_key = f"{topic}_{difficulty}"

        if mode == "solution":
            problem_json = user.get('last_problem_data')
            if problem_json and send_solution_dispatch(user, problem_json):
                success_count += 1
        else:
            if cache_key not in problem_cache:
                problem_cache[cache_key] = get_problem(topic, difficulty)
            
            problem_json = problem_cache[cache_key]
            if problem_json and send_morning_challenge(user, problem_json):
                sub_ref.document(user['id']).update({
                    "last_problem_data": problem_json,
                    "last_sent": datetime.now(timezone.utc),
                    "streak": user.get('streak', 0) + 1
                })
                success_count += 1

    print(f"🏁 Finished. Successful sends: {success_count}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        mode = "solution" if "solution" in arg else "morning"
    else:
        hour = datetime.now().hour
        mode = "morning" if 4 <= hour < 14 else "solution"
    
    run_dispatch(mode)

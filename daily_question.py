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
# Ensure this matches the APP_ID in your index.html
APP_ID = "leetcode-dsa-bot" 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")

# Firebase Initialization
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    try:
        if not service_account_json:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT secret is missing!")
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
        print("✅ AlgoPulse Engine: Firebase initialized successfully.")
    except Exception as e:
        print(f"❌ CRITICAL: Failed to initialize Firebase: {e}")
        exit(1)

db = firestore.client()

def clean_ai_response(text):
    """Universal cleaner to remove markdown blocks from AI responses."""
    if not text:
        return ""
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:])
        else:
            text = text.replace("```", "")
    return text.strip()

def call_ai(prompt, is_json=True):
    """Reliable AI call using Gemini 2.0 Flash."""
    # FIXED: Clean URL (removed markdown artifacts)
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
            print(f"❌ AI API Error ({res.status_code}): {res.text}")
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
    print(f"📨 Attempting to dispatch email to: {to}...")
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"❌ SMTP Error: Credentials missing (SENDER_EMAIL/SENDER_PASSWORD)")
        return False
    
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = f"AlgoPulse <{SENDER_EMAIL}>"
    msg['To'] = to
    msg.attach(MIMEText(body, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(SENDER_EMAIL, SENDER_PASSWORD)
            s.sendmail(SENDER_EMAIL, to, msg.as_string())
        print(f"✅ SMTP Success: Email delivered to {to}")
        return True
    except Exception as e:
        print(f"❌ SMTP Failure for {to}: {e}")
        return False

# --- Email Styling Helpers ---
EMAIL_BASE_CSS = """
    margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background-color: #f4f7fa; color: #1a202c; line-height: 1.6;
"""

def get_formal_morning_html(p, streak, difficulty, language):
    diff_color = {"Easy": "#10b981", "Medium": "#3b82f6", "Hard": "#ef4444"}.get(difficulty, "#3b82f6")
    return f"""
    <html>
    <body style="{EMAIL_BASE_CSS}">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="padding: 40px 20px;">
            <tr>
                <td align="center">
                    <table width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                        <tr>
                            <td style="padding: 40px; border-bottom: 1px solid #edf2f7;">
                                <div style="color: #718096; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px;">AlgoPulse Daily Dispatch</div>
                                <h1 style="margin: 0; font-size: 24px; color: #2d3748; font-weight: 800;">{p['title']}</h1>
                                <div style="margin-top: 16px;">
                                    <span style="background-color: {diff_color}; color: white; padding: 4px 12px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-right: 8px;">{difficulty.upper()}</span>
                                    <span style="color: #4a5568; font-size: 13px; font-weight: 600;">Day {streak} • {language}</span>
                                </div>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 40px;">
                                <h3 style="margin: 0 0 16px 0; font-size: 16px; color: #2d3748;">The Challenge</h3>
                                <p style="margin: 0; color: #4a5568; font-size: 15px;">{p['description']}</p>
                                
                                <div style="margin: 32px 0; padding: 20px; background-color: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0;">
                                    <h4 style="margin: 0 0 8px 0; font-size: 11px; color: #a0aec0; text-transform: uppercase;">Example Input/Output</h4>
                                    <code style="font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; color: #2d3748; font-size: 13px;">{p.get('example', 'Refer to LeetCode for detailed constraints.')}</code>
                                </div>

                                <div style="text-align: center;">
                                    <a href="[https://leetcode.com/problems/](https://leetcode.com/problems/){p['slug']}/" style="display: inline-block; background-color: #2d3748; color: #ffffff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 700; font-size: 14px;">Open Problem on LeetCode</a>
                                </div>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f8fafc; border-top: 1px solid #edf2f7; text-align: center;">
                                <p style="margin: 0; font-size: 12px; color: #718096;">Stay consistent. One problem a day builds a career.</p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

def get_formal_solution_html(p, solution, language):
    return f"""
    <html>
    <body style="{EMAIL_BASE_CSS}">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="padding: 40px 20px;">
            <tr>
                <td align="center">
                    <table width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                        <tr>
                            <td style="padding: 40px; border-bottom: 1px solid #edf2f7;">
                                <div style="color: #48bb78; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px;">AlgoPulse Recap</div>
                                <h1 style="margin: 0; font-size: 24px; color: #2d3748; font-weight: 800;">Solution: {p['title']}</h1>
                                <p style="margin: 8px 0 0 0; color: #718096; font-size: 14px;">Optimal implementation in <strong>{language}</strong></p>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 40px;">
                                <div style="background-color: #1a202c; border-radius: 8px; padding: 24px; overflow-x: auto;">
                                    <pre style="margin: 0; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 13px; line-height: 1.5; color: #e2e8f0;">{solution}</pre>
                                </div>
                                <p style="margin-top: 24px; color: #4a5568; font-size: 14px; text-align: center;">
                                    Compare this with your logic. Reviewing different approaches is key to mastering algorithms.
                                </p>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 20px 40px; background-color: #f8fafc; border-top: 1px solid #edf2f7; text-align: center;">
                                <p style="margin: 0; font-size: 11px; color: #a0aec0; text-transform: uppercase; font-weight: 700;">AlgoPulse • See you tomorrow at 07:00 AM IST</p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

if __name__ == "__main__":
    mode = "morning"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    
    if mode not in ["morning", "solution"]:
        hour = datetime.now(timezone.utc).hour
        mode = "morning" if hour < 12 else "solution"
        
    print(f"🚀 ENGINE START: Running in {mode.upper()} mode...")

    # Subscriber Logic
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    sub_list = [ {**doc.to_dict(), 'id': doc.id} for doc in subs ]
    
    print(f"👥 Database: Found {len(sub_list)} active subscribers.")

    if mode == "morning":
        # Cache unique problem requests
        configs = set((u.get('topic', 'Arrays'), u.get('difficulty', 'Medium')) for u in sub_list)
        packs = {f"{t}_{d}": get_problem(t, d) for t, d in configs}

        for u in sub_list:
            key = f"{u.get('topic', 'Arrays')}_{u.get('difficulty', 'Medium')}"
            problem_data = packs.get(key)
            if problem_data:
                p = json.loads(problem_data)
                streak = u.get('streak', 0) + 1
                body = get_formal_morning_html(p, streak, u['difficulty'], u['language'])
                
                subject = f"Day {streak}: {p['title']} ({u['difficulty']})"
                if dispatch_email(u['email'], subject, body):
                    sub_ref.document(u['id']).update({
                        'streak': streak,
                        'lastProblemData': problem_data,
                        'lastSentAt': datetime.now(timezone.utc)
                    })
            else:
                print(f"⚠️ No problem found for config '{key}'")

    elif mode == "solution":
        for u in sub_list:
            problem_data = u.get('lastProblemData')
            if not problem_data:
                print(f"⚠️ Skipping {u['email']}: No problem record found.")
                continue

            p = json.loads(problem_data)
            lang = u.get('language', 'Python')
            print(f"🛠️ Generating solution for '{p['title']}' for {u['email']}...")
            
            prompt = f"Provide a professional, clean, efficient {lang} code solution for the LeetCode problem: '{p['title']}'. Output ONLY raw code, no explanations."
            solution_code = call_ai(prompt, is_json=False)
            
            if solution_code:
                body = get_formal_solution_html(p, solution_code, lang)
                dispatch_email(u['email'], f"✅ Solution: {p['title']}", body)

    print(f"🏁 ENGINE FINISHED.")

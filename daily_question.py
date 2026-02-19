import os
import json
import smtplib
import sys
import requests
import firebase_admin
import time
import re
from datetime import datetime, timezone
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Configuration ---
# CRITICAL: Ensure this matches the APP_ID in your index.html exactly
APP_ID = "leetcode-dsa-bot" 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")

print(f"--- AlgoPulse Diagnostic Start [{datetime.now(timezone.utc)}] ---")

# 1. Firebase Initialization Diagnostics
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    try:
        if not service_account_json:
            print("❌ ERROR: FIREBASE_SERVICE_ACCOUNT secret is missing or empty.")
            sys.exit(1)
        
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
        print("✅ Firebase: Successfully initialized with service account.")
    except Exception as e:
        print(f"❌ Firebase: Initialization failed. Error: {e}")
        sys.exit(1)

db = firestore.client()

def clean_url(url):
    """
    Strips markdown formatting from URLs. 
    Prevents 'No connection adapters' error if strings are corrupted with [text](link).
    """
    if not url: return ""
    # Remove markdown link syntax: [title](url) -> url
    url = re.sub(r'\[.*?\]\((.*?)\)', r'\1', url)
    # Remove any leading/trailing brackets or parenthesis
    return url.strip('[]() ')

def clean_ai_response(text):
    """Universal cleaner to remove markdown blocks from AI responses."""
    if not text: return ""
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
    raw_url = f"[https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=](https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=){GEMINI_API_KEY}"
    url = clean_url(raw_url)
    
    print(f"🤖 AI: Requesting generation... (URL Protocol: {url[:8]})")
    
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
            print("✅ AI: Response received successfully.")
            return clean_ai_response(raw_text)
        else:
            print(f"❌ AI: API Error {res.status_code}. Response: {res.text[:200]}")
    except Exception as e:
        print(f"❌ AI: Connection failed. This is usually due to a malformed URL string. Error: {e}")
    return None

def refill_bank(topic, difficulty):
    print(f"🧠 Bank: Low on '{topic}' ({difficulty}). Generating 5 new problems...")
    prompt = (
        f"Generate exactly 5 unique LeetCode problems for '{topic}' at '{difficulty}' level. "
        "Return a JSON array of objects with: 'title', 'slug', 'description', 'constraints', 'example'. Output ONLY raw JSON."
    )
    raw = call_ai(prompt, is_json=True)
    if not raw: return False
    try:
        data = json.loads(raw)
        problems = data if isinstance(data, list) else data.get('problems', [])
        
        bank_path = f"artifacts/{APP_ID}/public/data/question_bank"
        bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
        
        for p in problems:
            bank_ref.add({
                "topic": topic, "difficulty": difficulty, "problem_data": json.dumps(p),
                "used": False, "createdAt": datetime.now(timezone.utc)
            })
        print(f"✅ Bank: Refilled {len(problems)} problems at {bank_path}")
        return True
    except Exception as e:
        print(f"❌ Bank: JSON Parse/Save error: {e}")
        return False

def get_problem(topic, difficulty):
    """Fetches one unused problem from the bank."""
    print(f"🔍 Firestore: Searching for '{topic}' ({difficulty}) problem...")
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    
    query = bank_ref.where(filter=FieldFilter("topic", "==", topic)) \
                    .where(filter=FieldFilter("difficulty", "==", difficulty)) \
                    .where(filter=FieldFilter("used", "==", False)) \
                    .limit(1).stream()
    
    for doc in query:
        print(f"🎯 Firestore: Found problem document ID: {doc.id}")
        bank_ref.document(doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return doc.to_dict()["problem_data"]
    
    print("⚠️ Firestore: No unused problems in bank. Triggering refill...")
    if refill_bank(topic, difficulty):
        time.sleep(2) # Consistency delay
        return get_problem(topic, difficulty)
    return None

def dispatch_email(to, subject, body):
    print(f"📨 Mail: Attempting delivery to {to}...")
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("❌ Mail: Credentials missing (EMAIL_SENDER/EMAIL_PASSWORD secrets).")
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
        print(f"✅ Mail: Successfully delivered to {to}")
        return True
    except Exception as e:
        print(f"❌ Mail: SMTP failure for {to}. Error: {e}")
        return False

# --- Email Styling Helpers ---
EMAIL_BASE_CSS = """
    margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background-color: #f4f7fa; color: #1a202c; line-height: 1.6;
"""

def get_formal_morning_html(p, streak, difficulty, language):
    diff_color = {"Easy": "#10b981", "Medium": "#3b82f6", "Hard": "#ef4444"}.get(difficulty, "#3b82f6")
    # Using clean_url on the href for safety
    problem_url = clean_url(f"[https://leetcode.com/problems/](https://leetcode.com/problems/){p['slug']}/")
    
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
                                    <code style="font-family: monospace; color: #2d3748; font-size: 13px;">{p.get('example', 'Refer to LeetCode for details.')}</code>
                                </div>
                                <div style="text-align: center;">
                                    <a href="{problem_url}" style="display: inline-block; background-color: #2d3748; color: #ffffff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 700; font-size: 14px;">Open Problem on LeetCode</a>
                                </div>
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
    
    print(f"🚀 MODE: {mode.upper()}")
    
    # Path Diagnostics
    sub_path = f"artifacts/{APP_ID}/public/data/subscribers"
    print(f"📂 Firestore Path: {sub_path}")
    
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    
    # 2. Detailed Subscriber Query
    all_docs = sub_ref.stream()
    sub_list = []
    total_found = 0
    
    for doc in all_docs:
        total_found += 1
        data = doc.to_dict()
        if data.get('status') == 'active':
            sub_list.append({**data, 'id': doc.id})
            
    print(f"👥 Database: Found {total_found} total documents. {len(sub_list)} are 'active'.")

    if not sub_list:
        print("🛑 STOP: No active subscribers to process.")
        sys.exit(0)

    if mode == "morning":
        for u in sub_list:
            print(f"\n👉 Processing User: {u['email']}")
            t = u.get('topic', 'LogicBuilding')
            d = u.get('difficulty', 'Medium')
            
            problem_data = get_problem(t, d)
            if problem_data:
                p = json.loads(problem_data)
                streak = u.get('streak', 0) + 1
                body = get_formal_morning_html(p, streak, d, u.get('language', 'Python'))
                
                if dispatch_email(u['email'], f"Day {streak}: {p['title']} ({d})", body):
                    sub_ref.document(u['id']).update({
                        'streak': streak,
                        'lastProblemData': problem_data,
                        'lastSentAt': datetime.now(timezone.utc)
                    })
                    print(f"✅ Success: Updated user streak and lastProblemData.")
            else:
                print(f"❌ Failed: Could not retrieve or generate problem for Track: {t}_{d}")

    elif mode == "solution":
        for u in sub_list:
            print(f"\n👉 Processing Solution for: {u['email']}")
            p_data = u.get('lastProblemData')
            if not p_data:
                print(f"⚠️ Warning: User has no 'lastProblemData'. (Morning run may have failed or was never run).")
                continue
            
            p = json.loads(p_data)
            lang = u.get('language', 'Python')
            prompt = f"Provide a professional {lang} solution for LeetCode: '{p['title']}'. Code only."
            
            sol_code = call_ai(prompt, is_json=False)
            if sol_code:
                # Reuse formal styles for solution
                body = f"<pre style='background:#f8fafc; padding:20px; border-radius:8px;'>{sol_code}</pre>"
                dispatch_email(u['email'], f"✅ Solution: {p['title']}", body)

    print(f"\n--- AlgoPulse Dispatch Finished ---")

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
    # Fixed the URL which contained markdown link artifacts in the previous version
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
            print(f"❌ AI API Error: {res.status_code} - {res.text}")
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
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print(f"❌ SMTP Error: Credentials missing (SENDER_EMAIL/SENDER_PASSWORD)")
        return False
    
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = f"AlgoPulse Engine <{SENDER_EMAIL}>"
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
                streak = u.get('streak', 0) + 1
                body = f"""
                <html>
                <body style="margin: 0; padding: 0; background-color: #020617; font-family: 'Inter', Helvetica, Arial, sans-serif; color: #f8fafc;">
                    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #020617; padding: 40px 20px;">
                        <tr>
                            <td align="center">
                                <table width="100%" max-width="600" style="max-width: 600px; background-color: #0f172a; border: 1px solid #1e293b; border-radius: 24px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);">
                                    <!-- Header -->
                                    <tr>
                                        <td style="padding: 40px 40px 20px 40px; text-align: center;">
                                            <div style="display: inline-block; background-color: #1d4ed8; color: #ffffff; padding: 8px 16px; border-radius: 100px; font-weight: 800; font-size: 12px; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 24px;">
                                                Day {streak} Pulse
                                            </div>
                                            <h1 style="margin: 0; font-size: 32px; font-weight: 900; letter-spacing: -1px; color: #ffffff; line-height: 1.2;">{p['title']}</h1>
                                        </td>
                                    </tr>
                                    <!-- Content -->
                                    <tr>
                                        <td style="padding: 20px 40px 40px 40px;">
                                            <div style="background-color: #020617; border-radius: 16px; border-left: 4px solid #3b82f6; padding: 24px; margin-bottom: 32px;">
                                                <p style="margin: 0; font-size: 16px; line-height: 1.7; color: #94a3b8;">{p['description']}</p>
                                            </div>
                                            
                                            <div style="background-color: #1e293b; border-radius: 16px; padding: 20px; margin-bottom: 40px;">
                                                <h4 style="margin: 0 0 12px 0; font-size: 11px; font-weight: 900; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Input Example</h4>
                                                <code style="font-family: 'JetBrains Mono', 'Courier New', monospace; color: #38bdf8; font-size: 14px; line-height: 1.5;">{p.get('example', 'Check LeetCode for details')}</code>
                                            </div>

                                            <div style="text-align: center;">
                                                <a href="[https://leetcode.com/problems/](https://leetcode.com/problems/){p['slug']}/" style="display: inline-block; background-color: #2563eb; color: #ffffff; padding: 18px 48px; border-radius: 16px; text-decoration: none; font-weight: 800; font-size: 16px; box-shadow: 0 10px 15px -3px rgba(37, 99, 235, 0.4);">Solve Problem</a>
                                            </div>
                                        </td>
                                    </tr>
                                    <!-- Footer -->
                                    <tr>
                                        <td style="padding: 20px 40px 40px 40px; border-top: 1px solid #1e293b; text-align: center;">
                                            <p style="margin: 0; font-size: 12px; color: #475569; letter-spacing: 1px; text-transform: uppercase; font-weight: 700;">AlgoPulse • Consistency is the new Intensity</p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </body>
                </html>
                """
                if dispatch_email(u['email'], f"🚀 Day {streak}: {p['title']}", body):
                    sub_ref.document(u['id']).update({
                        'streak': streak,
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
            
            prompt = f"Provide a clean, efficient {lang} code solution for the LeetCode problem: '{p['title']}'. Output ONLY the raw code without any explanations."
            solution = call_ai(prompt, is_json=False)
            
            if solution:
                body = f"""
                <html>
                <body style="margin: 0; padding: 0; background-color: #020617; font-family: 'Inter', Helvetica, Arial, sans-serif; color: #f8fafc;">
                    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #020617; padding: 40px 20px;">
                        <tr>
                            <td align="center">
                                <table width="100%" max-width="600" style="max-width: 600px; background-color: #0f172a; border: 1px solid #1e293b; border-radius: 24px; overflow: hidden;">
                                    <!-- Header -->
                                    <tr>
                                        <td style="padding: 40px 40px 20px 40px;">
                                            <div style="color: #10b981; font-weight: 800; font-size: 12px; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 8px;">Solution Recap</div>
                                            <h1 style="margin: 0; font-size: 28px; font-weight: 900; color: #ffffff; line-height: 1.2;">{p['title']}</h1>
                                            <div style="margin-top: 8px; font-size: 14px; color: #64748b;">Language: <span style="color: #f8fafc; font-weight: 700;">{lang}</span></div>
                                        </td>
                                    </tr>
                                    <!-- Code Block -->
                                    <tr>
                                        <td style="padding: 20px 40px 40px 40px;">
                                            <div style="background-color: #020617; border-radius: 16px; border: 1px solid #334155; padding: 24px; overflow-x: auto;">
                                                <pre style="margin: 0; font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 13px; line-height: 1.6; color: #34d399;">{solution}</pre>
                                            </div>
                                            <div style="margin-top: 24px; text-align: center;">
                                                <p style="font-size: 14px; color: #94a3b8; line-height: 1.5;">Compare this with your implementation. Great job staying consistent today!</p>
                                            </div>
                                        </td>
                                    </tr>
                                    <!-- Footer -->
                                    <tr>
                                        <td style="padding: 20px 40px 40px 40px; border-top: 1px solid #1e293b; text-align: center;">
                                            <p style="margin: 0; font-size: 11px; color: #475569; letter-spacing: 1px; text-transform: uppercase; font-weight: 700;">AlgoPulse Recap • See you tomorrow at 07:00 AM</p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </body>
                </html>
                """
                if dispatch_email(u['email'], f"✅ Solution: {p['title']}", body):
                    print(f"✅ Evening solution sent to {u['email']}")

    print(f"🏁 ENGINE FINISHED.")

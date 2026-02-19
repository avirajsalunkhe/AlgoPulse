AlgoPulse ✨

AlgoPulse is a professional study automation engine that delivers high-quality LeetCode DSA problems and solutions to developers. By focusing on consistency rather than cramming, AlgoPulse helps software engineers build a permanent knowledge base through daily, personalized challenges.

The Rhythm ⚡

7:00 AM IST (The Challenge): A targeted LeetCode problem arrives in your inbox based on your chosen difficulty (Easy/Medium/Hard), Language, and Topic.

8:00 PM IST (The Solution): A clean, optimized solution in your preferred programming language (Python/Java/C++/JS) is delivered for that specific problem.

Technical Architecture 🏗️

AlgoPulse is built as a fully serverless ecosystem, ensuring zero operational costs and 100% reliability.

Component

Technology

Role

The Brain

Python 3.11

Dual-dispatch logic & AI integration

Database

Firebase Firestore

User state & Question bank management

Orchestrator

GitHub Actions

Cron scheduling (01:30 & 14:30 UTC)

AI Layer

Gemini 2.0 Flash

Context-aware problem & solution generation

Interface

Tailwind CSS

Subscriber Management Dashboard

System Workflow ⚙️

Trigger: GitHub Action fires twice daily via cron jobs.

Context Retrieval: The system queries Firestore for active subscribers and their specific track preferences (e.g., "Medium Arrays in Python").

Smart Fetching: If the internal Question Bank is low for a specific Topic/Difficulty, the AI generates a new batch of 10 problems.

Personalized Dispatch: * Morning: Sends the problem statement and LeetCode link.

Evening: Generates and sends a copy-paste ready code solution for the same problem.

Progress Tracking: Subscriber streaks are incremented upon successful morning delivery.

Installation & Deployment 🛠️

1. Firebase Setup

Enable Anonymous Authentication.

Create a Firestore Database in test mode or with appropriate rules.

Generate a Service Account JSON (Project Settings > Service Accounts).

Register a Web App to get your Firebase Config JSON.

2. GitHub Secrets

Add the following secrets to your repository (Settings > Secrets and variables > Actions):

Secret Name

Description

GEMINI_API_KEY

Your Google Gemini API Key

GROQ_API_KEY

(Optional) Groq API Key for fallback

EMAIL_SENDER

Your Gmail address

EMAIL_PASSWORD

Your Gmail App Password

FIREBASE_SERVICE_ACCOUNT

The content of your Service Account JSON

FIREBASE_CONFIG

Your Frontend Firebase Config JSON

3. Launch

Push your code to the main branch. GitHub Actions will automatically deploy your dashboard to GitHub Pages and start the dispatch cycle.

Author

Aviraj Salunkhe
Consistent learning for modern engineers.

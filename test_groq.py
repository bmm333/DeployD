import os

from dotenv import load_dotenv

load_dotenv()
print("GROQ_API_KEY:", os.environ.get("GROQ_API_KEY")[:10] + "...")

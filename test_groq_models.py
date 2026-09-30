from dotenv import load_dotenv

load_dotenv()
from agno.agent import Agent  # noqa: E402
from agno.models.groq import Groq  # noqa: E402

try:
    Agent(model=Groq(id="qwen/qwen3.8-27b")).run("hello")
    print("qwen worked")
except Exception as e:
    print("qwen failed:", e)

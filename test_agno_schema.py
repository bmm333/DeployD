from dotenv import load_dotenv

load_dotenv()
from agno.agent import Agent  # noqa: E402
from agno.models.groq import Groq  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class MySchema(BaseModel):
    name: str
    age: int


agent = Agent(
    model=Groq(id="llama-3.3-70b-versatile"), output_schema=MySchema, structured_outputs=True
)

res = agent.run("My name is John and I am 30 years old")
print("Type of res.content:", type(res.content))
print("Content:", res.content)
if hasattr(res, "parsed"):
    print("Parsed:", res.parsed)

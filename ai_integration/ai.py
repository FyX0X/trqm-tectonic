from google import genai

client = None
with open("api_key.txt") as f:
    client = genai.Client(api_key=f.read())


def call_ai(prompt):
    return client.models.generate_content(model="gemini-3.1-flash-lite", contents=prompt).text
cd D:\Ã÷Ä©
.venv\Scripts\activate
$env:OPENAI_API_KEY="sk-73a60e958b65473da508f919f87bc3f3"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-v4-flash"
python -m uvicorn web_app:app --host 127.0.0.1 --port 8010
pause
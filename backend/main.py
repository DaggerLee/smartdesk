import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from database import Base, engine
from routers import chat, knowledge_base

# 自动建表
Base.metadata.create_all(bind=engine)

app = FastAPI(title="SmartDesk API", version="1.0.0")

# 允许前端开发服务器跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(knowledge_base.router)
app.include_router(chat.router)


@app.get("/")
def root():
    return {"message": "SmartDesk API is running", "version": "1.0.0"}


@app.get("/health")
def health():
    api_key = os.getenv("GEMINI_API_KEY", "")
    return {
        "status": "ok",
        "gemini_configured": bool(api_key and api_key != "your_gemini_api_key_here"),
    }

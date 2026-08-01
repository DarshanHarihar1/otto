# app/main.py
from fastapi import FastAPI

from app.routes.webhook import router as webhook_router

app = FastAPI(title="otto")
app.include_router(webhook_router)


@app.get("/health")
def health():
    return {"status": "ok"}

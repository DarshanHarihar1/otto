# app/main.py
from fastapi import FastAPI

from app.routes.prava_callback import router as prava_router
from app.routes.webhook import router as webhook_router

app = FastAPI(title="otto")
app.include_router(webhook_router)
app.include_router(prava_router)


@app.get("/health")
def health():
    return {"status": "ok"}

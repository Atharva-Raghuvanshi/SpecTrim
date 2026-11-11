from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Query Optimizer Service")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "OK"

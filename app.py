from fastapi import FastAPI

app = FastAPI(title="Helena Alpha Engine v1")

@app.get("/health")
def health():
    return {"status": "ok", "service": "helena-alpha-engine"}

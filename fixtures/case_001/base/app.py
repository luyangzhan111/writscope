from fastapi import FastAPI

app = FastAPI()

@app.get("/api/v1/profile")
def profile():
    return {"status": "active"}

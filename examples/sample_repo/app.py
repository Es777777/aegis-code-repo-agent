from fastapi import FastAPI

from services.user_service import UserService

app = FastAPI()
service = UserService()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/users")
def create_user(payload: dict):
    return service.create(payload)

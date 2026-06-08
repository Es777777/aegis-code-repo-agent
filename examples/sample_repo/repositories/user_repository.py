class UserRepository:
    def __init__(self):
        self.rows = []

    def save(self, payload: dict):
        self.rows.append(payload)
        return {"id": len(self.rows), **payload}

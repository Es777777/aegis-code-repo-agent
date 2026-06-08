from repositories.user_repository import UserRepository


class UserService:
    def __init__(self):
        self.repository = UserRepository()

    def create(self, payload: dict):
        return self.repository.save(payload)

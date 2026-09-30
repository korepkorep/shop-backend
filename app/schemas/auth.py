import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128, examples=["Secret123"])

    @field_validator("password")
    @classmethod
    def password_rules(cls, value: str) -> str:
        # US-01 AC 2: хотя бы одна буква и одна цифра
        if not re.search(r"[A-Za-zА-Яа-я]", value) or not re.search(r"\d", value):
            raise ValueError("Пароль должен содержать хотя бы одну букву и одну цифру")
        return value


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["buyer@example.com"])
    password: str = Field(examples=["Buyer12345"])


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime

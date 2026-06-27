from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=20, pattern=r"^[a-z0-9_]+$")
    displayName: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    displayName: str
    bio: str
    avatarUrl: str | None


class MeResponse(UserResponse):
    email: str


class UserProfileResponse(UserResponse):
    followersCount: int
    followingCount: int
    isFollowing: bool


class CreatePostRequest(BaseModel):
    content: str = Field(min_length=1, max_length=280)


class PostResponse(BaseModel):
    id: int
    content: str
    createdAt: datetime
    author: UserResponse
    likeCount: int
    likedByMe: bool


class UpdateProfileRequest(BaseModel):
    displayName: str | None = Field(default=None, min_length=1, max_length=50)
    bio: str | None = Field(default=None, max_length=160)
    avatarUrl: str | None = Field(default=None, max_length=500)


class AvatarUploadRequest(BaseModel):
    contentType: str


class AvatarUploadResponse(BaseModel):
    uploadUrl: str
    publicUrl: str


class CreateConversationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)


class MessageResponse(BaseModel):
    id: int
    conversationId: int
    senderId: int
    content: str
    createdAt: datetime


class ConversationResponse(BaseModel):
    id: int
    partner: UserResponse
    lastMessage: MessageResponse | None


class MessageOnlyResponse(BaseModel):
    message: str

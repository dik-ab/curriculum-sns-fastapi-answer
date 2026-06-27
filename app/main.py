from http.cookies import SimpleCookie
from secrets import token_urlsafe

import socketio
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as RawResponse
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal, get_db, init_db
from .models import Conversation, EmailVerificationToken, Follow, Like, Message, Post, User
from .schemas import (
    AvatarUploadRequest,
    AvatarUploadResponse,
    ConversationResponse,
    CreateConversationRequest,
    CreatePostRequest,
    LoginRequest,
    MeResponse,
    MessageOnlyResponse,
    MessageResponse,
    PostResponse,
    RegisterRequest,
    UpdateProfileRequest,
    UserProfileResponse,
    UserResponse,
)
from .security import (
    clear_session_cookie,
    create_session_token,
    decode_session_token,
    get_current_user,
    hash_password,
    set_session_cookie,
    verify_password,
)

settings = get_settings()
app = FastAPI(title="Curriculum SNS FastAPI Answer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=[settings.frontend_url],
    cors_credentials=True,
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        displayName=user.display_name,
        bio=user.bio,
        avatarUrl=user.avatar_url,
    )


def message_response(message: Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversationId=message.conversation_id,
        senderId=message.sender_id,
        content=message.content,
        createdAt=message.created_at,
    )


def post_response(db: Session, post: Post, current_user_id: int) -> PostResponse:
    author = db.get(User, post.author_id)
    like_count = db.scalar(select(func.count()).select_from(Like).where(Like.post_id == post.id)) or 0
    liked = db.get(Like, {"user_id": current_user_id, "post_id": post.id}) is not None
    return PostResponse(
        id=post.id,
        content=post.content,
        createdAt=post.created_at,
        author=user_response(author),
        likeCount=like_count,
        likedByMe=liked,
    )


def pair_ids(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def get_conversation_or_404(db: Session, conversation_id: int, user_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or user_id not in (conversation.user_one_id, conversation.user_two_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会話が見つかりません")
    return conversation


def conversation_response(db: Session, conversation: Conversation, current_user_id: int) -> ConversationResponse:
    partner_id = conversation.user_two_id if conversation.user_one_id == current_user_id else conversation.user_one_id
    partner = db.get(User, partner_id)
    last_message = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(1)
    )
    return ConversationResponse(
        id=conversation.id,
        partner=user_response(partner),
        lastMessage=message_response(last_message) if last_message else None,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/register", response_model=MessageOnlyResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> MessageOnlyResponse:
    exists = db.scalar(select(User).where(or_(User.email == payload.email, User.username == payload.username)))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="メールアドレスまたはユーザー名は既に使われています")
    user = User(
        email=payload.email,
        username=payload.username,
        display_name=payload.displayName,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    token = token_urlsafe(32)
    db.add(EmailVerificationToken(user_id=user.id, token=token))
    db.commit()
    print(f"メール確認URL: {settings.frontend_url}/#/verify-email?token={token}")
    return MessageOnlyResponse(message="確認メールを送りました")


@app.get("/auth/verify-email", response_model=MessageOnlyResponse)
def verify_email(token: str, db: Session = Depends(get_db)) -> MessageOnlyResponse:
    record = db.scalar(select(EmailVerificationToken).where(EmailVerificationToken.token == token))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="確認トークンが見つかりません")
    user = db.get(User, record.user_id)
    user.email_verified = True
    db.delete(record)
    db.commit()
    return MessageOnlyResponse(message="メールアドレスを確認しました")


@app.post("/auth/login", response_model=MessageOnlyResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> MessageOnlyResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="メールアドレスまたはパスワードが違います")
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="メールアドレスを確認してください")
    set_session_cookie(response, create_session_token(user.id))
    return MessageOnlyResponse(message="ログインしました")


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> RawResponse:
    response = RawResponse(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@app.get("/auth/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        id=current_user.id,
        username=current_user.username,
        displayName=current_user.display_name,
        bio=current_user.bio,
        avatarUrl=current_user.avatar_url,
        email=current_user.email,
    )


@app.post("/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: CreatePostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    post = Post(content=payload.content, author_id=current_user.id)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post_response(db, post, current_user.id)


@app.get("/posts", response_model=list[PostResponse])
def list_posts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PostResponse]:
    posts = db.scalars(select(Post).order_by(desc(Post.created_at), desc(Post.id))).all()
    return [post_response(db, post, current_user.id) for post in posts]


@app.get("/posts/timeline", response_model=list[PostResponse])
def following_timeline(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PostResponse]:
    followee_ids = db.scalars(select(Follow.followee_id).where(Follow.follower_id == current_user.id)).all()
    allowed_ids = [current_user.id, *followee_ids]
    posts = db.scalars(select(Post).where(Post.author_id.in_(allowed_ids)).order_by(desc(Post.created_at), desc(Post.id))).all()
    return [post_response(db, post, current_user.id) for post in posts]


@app.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RawResponse:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="投稿が見つかりません")
    if post.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="他人の投稿は削除できません")
    db.delete(post)
    db.commit()
    return RawResponse(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/posts/{post_id}/likes", status_code=status.HTTP_204_NO_CONTENT)
def like_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RawResponse:
    if db.get(Post, post_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="投稿が見つかりません")
    if db.get(Like, {"user_id": current_user.id, "post_id": post_id}) is None:
        db.add(Like(user_id=current_user.id, post_id=post_id))
        db.commit()
    return RawResponse(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/posts/{post_id}/likes", status_code=status.HTTP_204_NO_CONTENT)
def unlike_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RawResponse:
    like = db.get(Like, {"user_id": current_user.id, "post_id": post_id})
    if like is not None:
        db.delete(like)
        db.commit()
    return RawResponse(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/users/{username}", response_model=UserProfileResponse)
def get_user_profile(
    username: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")
    followers = db.scalar(select(func.count()).select_from(Follow).where(Follow.followee_id == user.id)) or 0
    following = db.scalar(select(func.count()).select_from(Follow).where(Follow.follower_id == user.id)) or 0
    is_following = db.get(Follow, {"follower_id": current_user.id, "followee_id": user.id}) is not None
    return UserProfileResponse(
        id=user.id,
        username=user.username,
        displayName=user.display_name,
        bio=user.bio,
        avatarUrl=user.avatar_url,
        followersCount=followers,
        followingCount=following,
        isFollowing=is_following,
    )


@app.get("/users/{username}/posts", response_model=list[PostResponse])
def get_user_posts(username: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PostResponse]:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")
    posts = db.scalars(select(Post).where(Post.author_id == user.id).order_by(desc(Post.created_at), desc(Post.id))).all()
    return [post_response(db, post, current_user.id) for post in posts]


@app.post("/users/{username}/follow", status_code=status.HTTP_204_NO_CONTENT)
def follow_user(username: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RawResponse:
    target = db.scalar(select(User).where(User.username == username))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")
    if target.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="自分自身はフォローできません")
    if db.get(Follow, {"follower_id": current_user.id, "followee_id": target.id}) is None:
        db.add(Follow(follower_id=current_user.id, followee_id=target.id))
        db.commit()
    return RawResponse(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/users/{username}/follow", status_code=status.HTTP_204_NO_CONTENT)
def unfollow_user(username: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RawResponse:
    target = db.scalar(select(User).where(User.username == username))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")
    follow = db.get(Follow, {"follower_id": current_user.id, "followee_id": target.id})
    if follow is not None:
        db.delete(follow)
        db.commit()
    return RawResponse(status_code=status.HTTP_204_NO_CONTENT)


@app.patch("/users/me", response_model=MeResponse)
def update_me(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    if payload.displayName is not None:
        current_user.display_name = payload.displayName
    if payload.bio is not None:
        current_user.bio = payload.bio
    if payload.avatarUrl is not None:
        current_user.avatar_url = payload.avatarUrl
    db.commit()
    db.refresh(current_user)
    return me(current_user)


@app.post("/users/me/avatar-upload-url", response_model=AvatarUploadResponse)
def avatar_upload_url(
    payload: AvatarUploadRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> AvatarUploadResponse:
    extension = "jpg" if payload.contentType == "image/jpeg" else "png"
    public_url = f"{str(request.base_url).rstrip('/')}/uploads/avatar/{current_user.id}.{extension}"
    return AvatarUploadResponse(uploadUrl=public_url, publicUrl=public_url)


@app.put("/uploads/avatar/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_avatar(filename: str) -> RawResponse:
    return RawResponse(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationResponse]:
    conversations = db.scalars(
        select(Conversation)
        .where(or_(Conversation.user_one_id == current_user.id, Conversation.user_two_id == current_user.id))
        .order_by(desc(Conversation.created_at), desc(Conversation.id))
    ).all()
    return [conversation_response(db, conversation, current_user.id) for conversation in conversations]


@app.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: CreateConversationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    partner = db.scalar(select(User).where(User.username == payload.username))
    if partner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ユーザーが見つかりません")
    if partner.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="自分自身とは会話できません")
    user_one_id, user_two_id = pair_ids(current_user.id, partner.id)
    conversation = db.scalar(
        select(Conversation).where(and_(Conversation.user_one_id == user_one_id, Conversation.user_two_id == user_two_id))
    )
    if conversation is None:
        conversation = Conversation(user_one_id=user_one_id, user_two_id=user_two_id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
    return conversation_response(db, conversation, current_user.id)


@app.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageResponse]:
    get_conversation_or_404(db, conversation_id, current_user.id)
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at, Message.id)
    ).all()
    return [message_response(message) for message in messages]


def user_id_from_environ(environ) -> int | None:
    cookie_header = environ.get("HTTP_COOKIE", "")
    cookie = SimpleCookie(cookie_header)
    morsel = cookie.get(settings.cookie_name)
    if morsel is None:
        return None
    try:
        return decode_session_token(morsel.value)
    except HTTPException:
        return None


@sio.event(namespace="/chat")
async def connect(sid, environ, auth):
    user_id = user_id_from_environ(environ)
    if user_id is None:
        return False
    await sio.save_session(sid, {"user_id": user_id}, namespace="/chat")
    return True


@sio.on("joinConversation", namespace="/chat")
async def join_conversation(sid, data):
    session = await sio.get_session(sid, namespace="/chat")
    conversation_id = int(data.get("conversationId"))
    with SessionLocal() as db:
        get_conversation_or_404(db, conversation_id, session["user_id"])
    await sio.enter_room(sid, f"conversation:{conversation_id}", namespace="/chat")


@sio.on("sendMessage", namespace="/chat")
async def send_message(sid, data):
    session = await sio.get_session(sid, namespace="/chat")
    conversation_id = int(data.get("conversationId"))
    content = str(data.get("content", "")).strip()
    if content == "" or len(content) > 1000:
        return
    with SessionLocal() as db:
        get_conversation_or_404(db, conversation_id, session["user_id"])
        message = Message(conversation_id=conversation_id, sender_id=session["user_id"], content=content)
        db.add(message)
        db.commit()
        db.refresh(message)
        payload = message_response(message).model_dump(mode="json")
    await sio.emit("newMessage", payload, room=f"conversation:{conversation_id}", namespace="/chat")


socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

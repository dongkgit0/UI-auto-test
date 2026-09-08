"""
UI 自动化测试平台 - 后端入口

启动方式（任选其一）：
    1. 直接运行脚本：
        python backend/main.py
    2. uvicorn 方式（开发推荐，支持热重载）：
        cd ui_test_platform
        pip install -r requirements.txt
        uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

启动后访问 http://localhost:8000 即可打开管理界面
"""
import os
import sys
import secrets

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# 支持直接以脚本方式运行（python backend/main.py）：
# 直接运行时 __package__ 为空，.database / .api 等相对导入会报
# "attempted relative import with no known parent package"；
# 这里把项目根目录加入 sys.path 并声明包名，保证相对导入正常解析。
if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "backend"

from .database import init_db
from .api import servers, testcases, runs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="UI 自动化测试平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(servers.router)
app.include_router(testcases.router)
app.include_router(runs.router)

# 截图静态资源
app.mount("/screenshots", StaticFiles(directory=os.path.join(BASE_DIR, "screenshots")), name="screenshots")
# 前端静态资源
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "frontend")), name="static")


# ---------- 登录认证 ----------

# 内存中的有效 token 集合（简单实现，重启后失效）
_valid_tokens: set = set()

# 账号密码（运行时可修改，重启后恢复默认）
ADMIN_USERNAME = "admin"
_current_password = "888888"


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/login")
def login(payload: LoginRequest):
    """登录验证，成功返回 token"""
    if payload.username == ADMIN_USERNAME and payload.password == _current_password:
        token = secrets.token_hex(16)
        _valid_tokens.add(token)
        return {"ok": True, "token": token, "username": payload.username}
    raise HTTPException(401, "用户名或密码错误")


@app.post("/api/change-password")
def change_password(payload: ChangePasswordRequest, token: str = ""):
    """修改密码，需要验证 token 和旧密码"""
    global _current_password
    if token not in _valid_tokens:
        raise HTTPException(401, "登录已过期，请重新登录")
    if payload.old_password != _current_password:
        raise HTTPException(400, "旧密码错误")
    if not payload.new_password or len(payload.new_password) < 4:
        raise HTTPException(400, "新密码至少4位")
    _current_password = payload.new_password
    return {"ok": True, "message": "密码修改成功"}


@app.post("/api/logout")
def logout(token: str = ""):
    """退出登录，移除 token"""
    if token in _valid_tokens:
        _valid_tokens.discard(token)
    return {"ok": True}


@app.get("/api/verify")
def verify(token: str = ""):
    """验证 token 是否有效"""
    return {"ok": token in _valid_tokens}


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "frontend", "index.html"))


@app.on_event("startup")
def on_startup():
    init_db()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

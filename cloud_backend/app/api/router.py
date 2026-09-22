"""路由总注册"""
from fastapi import FastAPI
from app.api import auth, users, teams, workspaces, documents, storage, sync, devices, sharing, admin, backup


def register_routers(app: FastAPI):
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(teams.router)
    app.include_router(workspaces.router)
    app.include_router(documents.router)
    app.include_router(storage.router)
    app.include_router(sync.router)
    app.include_router(devices.router)
    app.include_router(sharing.router)
    app.include_router(admin.router)
    app.include_router(backup.router, prefix="/api/backup")

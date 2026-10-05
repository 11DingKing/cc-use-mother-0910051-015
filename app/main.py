from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.routers import staff, themes, venues, schools, sessions, reviews, warnings, statistics, changes, ranking

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="遗产日活动排班管理系统 - 讲解员和讲师排班、场次管理、评价统计、变更联动重排"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(staff.router)
app.include_router(themes.router)
app.include_router(venues.router)
app.include_router(schools.router)
app.include_router(sessions.router)
app.include_router(reviews.router)
app.include_router(warnings.router)
app.include_router(statistics.router)
app.include_router(changes.router)
app.include_router(ranking.router)


@app.get("/", tags=["系统"])
def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "status": "running"
    }


@app.get("/health", tags=["系统"])
def health_check():
    return {"status": "healthy"}

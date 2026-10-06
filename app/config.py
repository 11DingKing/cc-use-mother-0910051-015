from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "遗产日活动排班管理系统"
    APP_VERSION: str = "1.0.0"
    DATABASE_URL: str = "sqlite:///./heritage_scheduling.db"
    DEBUG: bool = True

    STARTER_THRESHOLD: int = 5
    WARNING_STAFF_SHORTAGE_THRESHOLD: float = 0.3

    # 团体申请拆分方案的默认确认时限（小时）
    GROUP_CONFIRM_DEADLINE_HOURS: float = 48.0

    class Config:
        env_file = ".env"


settings = Settings()

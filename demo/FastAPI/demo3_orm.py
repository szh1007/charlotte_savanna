import os
from contextlib import asynccontextmanager
from datetime import datetime

import dotenv
from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, String, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

dotenv.load_dotenv()

username = os.getenv("MYSQL_USERNAME", "")
pwd = os.getenv("MYSQL_PASSWORD", "")
host = os.getenv("MYSQL_HOST", "")
port = os.getenv("MYSQL_PORT", "")
db_name = os.getenv("MYSQL_NAME", "")

""" 数据库引擎配置 """
async_engine = create_async_engine(
    f"mysql+aiomysql://{username}:{pwd}@{host}:{port}/{db_name}?charset=utf8mb4",
    echo=True,  # 输出 SQL 日志
    pool_size=10,  # 持久连接池大小
    max_overflow=20,  # 最大溢出连接数
)

""" 模型表格定义 """


# 基类模型
class CommonBaseModel(DeclarativeBase):
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=datetime.now,
        default=datetime.now,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        insert_default=datetime.now,
        default=datetime.now,
        onupdate=datetime.now,
        comment="更新时间",
    )


# 业务模型
class User(CommonBaseModel):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        comment="用户ID",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        comment="用户名",
    )
    age: Mapped[int] = mapped_column(
        default=18,
        comment="年龄",
    )
    phone: Mapped[str] = mapped_column(
        String(11),
        comment="手机号",
    )
    pwd: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="密码",
    )
    desc: Mapped[str] = mapped_column(
        String(255),
        nullable=True,
        comment="描述",
    )


""" 启动服务 创建表格 """


async def create_tables():
    async with async_engine.begin() as conn:
        await conn.run_sync(CommonBaseModel.metadata.create_all)


@asynccontextmanager  # 异步上下文管理器
async def lifespan(app: FastAPI):
    await create_tables()
    yield
    await async_engine.dispose()


app = FastAPI(lifespan=lifespan)


""" 创建数据库会话 """
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,  # 绑定数据库引擎
    class_=AsyncSession,  # 指定会话类
    expire_on_commit=False,  # 提交后会话不会过期
)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session  # 返回数据库会话给路由函数 -> 用 SQL 处理业务逻辑
            await session.commit()  # 等待提交事务
        except Exception:
            await session.rollback()  # 有异常, 则回滚事务
            raise
        finally:
            await session.close()  # 最终都会关闭会话


""" 业务函数 """


@app.get("/")
async def root():
    return {"result": "Hello World"}


@app.get("/user")
async def get_user_list(
    keywords: str | None,
    page: int = 1,
    page_size: int = 10,
    db: AsyncSession = Depends(get_db),
):
    base_sql = select(User).offset((page - 1) * page_size).limit(page_size)
    sql = base_sql.where(User.name.like(f"%{keywords}%")) if keywords else base_sql

    result = await db.execute(sql)
    users = result.scalars().all()
    return {"data": users}


@app.get("/user/{id}")
async def get_user(id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, id)
    return {"data": user}


class UserPostItem(BaseModel):
    name: str = Field("charlotte", min_length=2, max_length=10, description="用户名")
    age: int = Field(26, ge=18, le=60, description="年龄")
    phone: str = Field(
        ...,
        pattern=r"1[3456789]\d{9}",
        min_length=11,
        max_length=11,
        description="手机号",
    )
    pwd: str = Field(
        ...,
        pattern="^[a-zA-Z0-9]+$",
        min_length=12,
        max_length=18,
        description="密码",
    )
    desc: str | None = Field("", max_length=100, description="描述")


@app.post("/user")
async def create_user(item: UserPostItem, db: AsyncSession = Depends(get_db)):
    db.add(User(**item.__dict__))
    await db.commit()
    return {"data": item}


class UserUpdateItem(BaseModel):
    pwd: str | None = Field(
        None,
        pattern="^[a-zA-Z0-9]+$",
        min_length=12,
        max_length=18,
        description="密码",
    )


@app.patch("/user/{id}")
async def update_user(
    id: int, item: UserUpdateItem, db: AsyncSession = Depends(get_db)
):
    user = await db.get(User, id)
    if not user:
        return {"error": "user not found"}

    update_data = item.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    await db.commit()
    await db.refresh(user)
    return {"data": user}


@app.delete("/user/{id}")
async def delete_user(id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, id)
    if not user:
        return {"error": "user not found"}

    await db.delete(user)
    await db.commit()
    return {"data": user}

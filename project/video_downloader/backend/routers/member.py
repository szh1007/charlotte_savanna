"""POST /api/member + GET /api/member/status (T04 会员鉴权).

契约 (PRD §8): {key} → {is_member, expires_at, token};
错误密钥 401 明确拒绝; status 通过 X-Member-Token header 识别会话.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import MemberSession, get_member, member_manager
from ..schemas import MemberRequest, MemberResponse, MemberStatusResponse

router = APIRouter(tags=["member"])


@router.post("/api/member", response_model=MemberResponse)
def member_verify(req: MemberRequest) -> MemberResponse:
    """提交会员密钥: 通过则签发会话 token, 失败明确拒绝."""
    session = member_manager.verify_key(req.key)
    if session is None:
        raise HTTPException(status_code=401, detail="密钥无效")
    return MemberResponse(
        is_member=True, expires_at=session.expires_at, token=session.token
    )


@router.get("/api/member/status", response_model=MemberStatusResponse)
def member_status(
    member: MemberSession | None = Depends(get_member),
) -> MemberStatusResponse:
    """查询当前会话会员状态 (无 / 过期 token 视为免费用户, 不报错)."""
    if member is None:
        return MemberStatusResponse(is_member=False)
    return MemberStatusResponse(is_member=True, expires_at=member.expires_at)

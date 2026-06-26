from fastapi import Depends, HTTPException, status

from auth.jwt import get_current_user

ROLE_HIERARCHY = {
    "FIELD_WORKER": 1,
    "PHC_ADMIN": 2,
    "DISTRICT_OFFICER": 3,
    "STATE_ADMIN": 4,
    "SUPERADMIN": 5,
}


def require_role(*roles: str):
    min_level = min(ROLE_HIERARCHY[r] for r in roles)

    async def check(current_user=Depends(get_current_user)):
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(roles)}",
            )
        return current_user

    return check

from fastapi import HTTPException

ADMIN = "Admin"
MANAGER = "Manager"
STAFF = "Staff"

def check_role(user_role, allowed_roles):

    if user_role not in allowed_roles:

        raise HTTPException(
            status_code=403,
            detail="Access denied. You do not have permission to view this page."
        )
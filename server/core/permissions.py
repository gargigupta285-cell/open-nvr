# Copyright (c) 2026 OpenNVR
# This file is part of OpenNVR.
# 
# OpenNVR is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# OpenNVR is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with OpenNVR.  If not, see <https://www.gnu.org/licenses/>.

from typing import TypeVar

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.auth import get_current_active_user
from core.database import get_db
from models import Camera, User

T = TypeVar("T")


class PermissionChecker:
    """
    Reusable dependency for checking ownership and permissions.
    Reduces code duplication in routers.
    """

    def __init__(self, model_class: type[T], ownership_field: str = "owner_id"):
        self.model_class = model_class
        self.ownership_field = ownership_field

    def check(self, resource_id: int, current_user: User, db: Session) -> T:
        resource = (
            db.query(self.model_class)
            .filter(self.model_class.id == resource_id)
            .first()
        )

        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{self.model_class.__name__} not found",
            )

        # Superuser bypass
        if current_user.is_superuser:
            return resource

        # Check ownership
        if getattr(resource, self.ownership_field) != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )

        return resource


# Specific dependency for Camera (matches "camera_id" path parameter)
def get_camera_or_403(
    camera_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Camera:
    checker = PermissionChecker(Camera)
    return checker.check(camera_id, current_user, db)


def user_has_permission(user: User, permission_name: str) -> bool:
    """True when ``user`` holds ``permission_name`` (a name-based RBAC
    capability, e.g. ``apps.install``).

    Resolution mirrors ``GET /users/me/permissions`` exactly so a
    require-permission gate and the UI's permission listing can never
    disagree:

    * superusers hold every permission;
    * a role with the ``full_access`` wildcard permission holds every
      permission;
    * otherwise the named permission must be present on the user's role.
    """
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "role", None)
    if role is None:
        return False
    role_perms = getattr(role, "permissions", None) or []
    names = {p.name for p in role_perms}
    return "full_access" in names or permission_name in names


class RequirePermission:
    """FastAPI dependency factory that gates a route on a named RBAC
    permission — the reusable "require this capability" seam the app
    install endpoints need (there wasn't one before; permission checks
    were previously either superuser-only or ad-hoc in the router).

    Usage::

        require_apps_install = RequirePermission("apps.install")

        @router.post("/...")
        async def endpoint(user: User = Depends(require_apps_install)):
            ...

    Returns the authenticated ``User`` (so the route can audit the
    actor) or raises 403 when the permission is absent. It composes on
    top of ``get_current_active_user``, so an unauthenticated call still
    fails with the usual 401 first.
    """

    def __init__(self, permission_name: str):
        self.permission_name = permission_name
        # Give the callable instance a ``__name__`` so it introspects like
        # a plain function dependency (FastAPI + tests that read
        # ``dependant.call.__name__`` — see test_apps_registry).
        self.__name__ = f"require_permission[{permission_name}]"

    def __call__(
        self,
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if not user_has_permission(current_user, self.permission_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Requires the '{self.permission_name}' permission"
                ),
            )
        return current_user

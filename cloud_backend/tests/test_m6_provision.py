import uuid
import pytest
from app.database import SessionLocal
from app.models import User, TeamMember
from provision_mvp import provision


def test_provision_preserves_existing_account_and_member_roles():
    slug = 'm6-' + uuid.uuid4().hex[:12]
    with SessionLocal() as db:
        admin = provision(db, slug + '@example.com', '管理员', 'm6-pass-123', slug, '团队', '空间', 'owner', True)
    with SessionLocal() as db:
        member = provision(db, 'member-' + slug + '@example.com', '成员', 'm6-pass-123', slug, '团队', '空间')
        assert member['workspace_id'] == admin['workspace_id']
        assert not db.get(User, member['user_id']).is_superuser
        assert db.get(TeamMember, (member['team_id'], member['user_id'])).role == 'member'
    with SessionLocal() as db:
        with pytest.raises(ValueError, match='已存在'):
            provision(db, 'member-' + slug + '@example.com', '试图覆盖', 'new-pass-123', slug, '团队', '空间', 'owner', True)
    with SessionLocal() as db:
        assert db.get(User, member['user_id']).display_name == '成员'
        assert not db.get(User, member['user_id']).is_superuser


def test_first_member_cannot_leave_team_without_manager():
    with SessionLocal() as db:
        with pytest.raises(ValueError, match='首次创建'):
            provision(db, uuid.uuid4().hex + '@example.com', '成员', 'm6-pass-123', uuid.uuid4().hex, '团队', '空间')

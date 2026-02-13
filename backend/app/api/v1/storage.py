"""
存储位置管理路由
提供存储位置的增删改查功能
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path

from backend.app.database import get_db
from backend.app.models import StorageLocation
from backend.app.api.schemas import (
    CreateStorageLocationRequest,
    UpdateStorageLocationRequest,
    StorageLocationResponse
)
from backend.app.api.deps import get_current_user
from backend.app.core.encoding import safe_str, safe_print

router = APIRouter(tags=["存储位置管理"])


@router.get("/storage-locations", response_model=List[StorageLocationResponse])
async def get_storage_locations(
    db: Session = Depends(get_db)
    # 暂时不需要认证，允许所有用户查看存储位置
    # current_user = Depends(get_current_user)
):
    """
    获取所有存储位置列表
    """
    locations = db.query(StorageLocation).order_by(
        StorageLocation.is_default.desc(),
        StorageLocation.created_at.asc()
    ).all()
    return locations


@router.post("/storage-locations", response_model=StorageLocationResponse)
async def create_storage_location(
    request: CreateStorageLocationRequest,
    db: Session = Depends(get_db)
    # 暂时不需要认证
    # current_user = Depends(get_current_user)
):
    """
    创建新的存储位置
    """
    # 验证路径是否为绝对路径
    path_obj = Path(request.path)
    if not path_obj.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="存储路径必须是绝对路径"
        )
    
    # 验证路径是否存在且可写
    try:
        if not path_obj.exists():
            # 尝试创建目录
            path_obj.mkdir(parents=True, exist_ok=True)
        if not path_obj.is_dir():
            raise HTTPException(
                status_code=400,
                detail="存储路径必须是目录"
            )
        # 检查目录是否可写
        test_file = path_obj / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="存储路径不可写"
            )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail="没有权限访问该路径"
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"路径验证失败: {safe_str(e)}"
        )
    
    # 检查名称是否重复
    existing = db.query(StorageLocation).filter(
        StorageLocation.name == request.name
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="存储位置名称已存在"
        )
    
    # 检查路径是否重复
    existing_path = db.query(StorageLocation).filter(
        StorageLocation.path == str(path_obj.resolve())
    ).first()
    if existing_path:
        raise HTTPException(
            status_code=400,
            detail="该路径已被使用"
        )
    
    # 创建存储位置
    location = StorageLocation(
        name=request.name,
        path=str(path_obj.resolve()),
        enabled=request.enabled,
        is_default=False
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    
    safe_print(f"[存储位置] 创建成功: {location.name} -> {location.path}")
    return location


@router.put("/storage-locations/{location_id}", response_model=StorageLocationResponse)
async def update_storage_location(
    location_id: int,
    request: UpdateStorageLocationRequest,
    db: Session = Depends(get_db)
    # 暂时不需要认证
    # current_user = Depends(get_current_user)
):
    """
    更新存储位置
    """
    location = db.query(StorageLocation).filter(
        StorageLocation.id == location_id
    ).first()
    if not location:
        raise HTTPException(status_code=404, detail="存储位置不存在")
    
    # 更新名称
    if request.name is not None:
        # 检查名称是否与其他位置重复
        existing = db.query(StorageLocation).filter(
            StorageLocation.name == request.name,
            StorageLocation.id != location_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="存储位置名称已存在"
            )
        location.name = request.name
    
    # 更新路径
    if request.path is not None:
        path_obj = Path(request.path)
        if not path_obj.is_absolute():
            raise HTTPException(
                status_code=400,
                detail="存储路径必须是绝对路径"
            )
        
        # 验证路径
        try:
            if not path_obj.exists():
                path_obj.mkdir(parents=True, exist_ok=True)
            if not path_obj.is_dir():
                raise HTTPException(
                    status_code=400,
                    detail="存储路径必须是目录"
                )
            # 检查路径是否与其他位置重复
            existing_path = db.query(StorageLocation).filter(
                StorageLocation.path == str(path_obj.resolve()),
                StorageLocation.id != location_id
            ).first()
            if existing_path:
                raise HTTPException(
                    status_code=400,
                    detail="该路径已被使用"
                )
            location.path = str(path_obj.resolve())
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="没有权限访问该路径"
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"路径验证失败: {safe_str(e)}"
            )
    
    # 更新启用状态
    if request.enabled is not None:
        location.enabled = request.enabled
    
    db.commit()
    db.refresh(location)
    
    safe_print(f"[存储位置] 更新成功: {location.name}")
    return location


@router.delete("/storage-locations/{location_id}")
async def delete_storage_location(
    location_id: int,
    db: Session = Depends(get_db)
    # 暂时不需要认证
    # current_user = Depends(get_current_user)
):
    """
    删除存储位置
    """
    location = db.query(StorageLocation).filter(
        StorageLocation.id == location_id
    ).first()
    if not location:
        raise HTTPException(status_code=404, detail="存储位置不存在")
    
    # 不能删除默认存储位置
    if location.is_default:
        raise HTTPException(
            status_code=400,
            detail="不能删除默认存储位置"
        )
    
    # 检查是否有文件使用该存储位置
    from backend.app.models import File
    file_count = db.query(File).filter(
        File.storage_location_id == location_id
    ).count()
    if file_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"该存储位置下还有 {file_count} 个文件，无法删除"
        )
    
    db.delete(location)
    db.commit()
    
    safe_print(f"[存储位置] 删除成功: {location.name}")
    return {"success": True, "message": "存储位置已删除"}


@router.post("/storage-locations/{location_id}/set-default")
async def set_default_storage_location(
    location_id: int,
    db: Session = Depends(get_db)
    # 暂时不需要认证
    # current_user = Depends(get_current_user)
):
    """
    设置默认存储位置
    """
    location = db.query(StorageLocation).filter(
        StorageLocation.id == location_id
    ).first()
    if not location:
        raise HTTPException(status_code=404, detail="存储位置不存在")
    
    if not location.enabled:
        raise HTTPException(
            status_code=400,
            detail="不能将禁用的存储位置设置为默认"
        )
    
    # 取消其他位置的默认状态
    db.query(StorageLocation).filter(
        StorageLocation.is_default == True
    ).update({"is_default": False})
    
    # 设置当前位置为默认
    location.is_default = True
    db.commit()
    db.refresh(location)
    
    safe_print(f"[存储位置] 设置默认成功: {location.name}")
    return {"success": True, "message": "默认存储位置已更新"}

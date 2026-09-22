"""
服务端 OSS 客户端
兼容 MinIO（本地开发）和阿里云 OSS（生产）
使用 boto3（S3 兼容协议）

阿里云 OSS endpoint 示例：
  https://oss-cn-hangzhou.aliyuncs.com  （境内）
  https://oss-cn-hongkong.aliyuncs.com  （境外）

MinIO 本地 endpoint 示例：
  http://localhost:9001
"""
import os
import boto3
from botocore.client import Config
from app.config import settings

# boto3/botocore 通过 urllib3 发请求，会读取 HTTP_PROXY 环境变量。
# Clash 等代理工具设置了全局代理，会拦截对 localhost MinIO 的请求并返回 502。
# 在此处修改 NO_PROXY 让 boto3 对 localhost 直连，不走系统代理。
# 必须在 boto3 client 创建之前设置（此模块顶部是最早时机）。
_current_no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
if "localhost" not in _current_no_proxy:
    os.environ["NO_PROXY"] = f"localhost,127.0.0.1,{_current_no_proxy}".rstrip(",")

_client = None


def _is_aliyun_oss() -> bool:
    """判断是否为阿里云 OSS（通过 endpoint 包含 aliyuncs.com 判断）"""
    return "aliyuncs.com" in settings.oss_endpoint


def get_oss_client():
    """
    获取 boto3 S3 客户端（单例，进程级复用）

    阿里云 OSS 注意事项：
    - 使用 s3v4 签名（OSS 支持）
    - region_name 设为 "oss-cn-hangzhou" 等对应 region（或保持 us-east-1 兼容）
    - endpoint_url 必须为 HTTPS（生产环境）
    """
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.oss_endpoint,
            aws_access_key_id=settings.oss_access_key_id,
            aws_secret_access_key=settings.oss_access_key_secret,
            config=Config(
                signature_version="s3v4",
                # 使用 path 寻址风格而非 virtual-hosted 风格：
                # virtual 生成 http://{bucket}.localhost:9001/key，bucket 子域名
                # 无法被本地 httpx mounts 的 localhost 规则命中，导致经过 Clash 代理返回 502。
                # path 生成 http://localhost:9001/{bucket}/key，host 为纯 localhost，
                # 可被正确绕过代理；阿里云 OSS 同样支持 path 风格。
                s3={"addressing_style": "path"},
            ),
            # 阿里云 OSS 使用 s3v4，region 填任意字符串均可
            region_name="us-east-1",
        )
    return _client


def reset_oss_client():
    """重置 OSS 客户端（配置变更后调用）"""
    global _client
    _client = None


def ensure_bucket_exists():
    """
    确保 OSS Bucket 存在
    - MinIO 开发环境：自动创建
    - 阿里云 OSS 生产环境：Bucket 由运维预先创建，此处仅做探活检查
    """
    client = get_oss_client()
    try:
        client.head_bucket(Bucket=settings.oss_bucket_name)
    except Exception:
        if not _is_aliyun_oss():
            # 仅在非阿里云环境（MinIO）自动创建 Bucket
            try:
                client.create_bucket(Bucket=settings.oss_bucket_name)
                print(f"OSS Bucket 已创建：{settings.oss_bucket_name}")
            except Exception as e:
                print(f"创建 Bucket 失败（可能已存在）：{e}")
        else:
            # 阿里云生产环境：Bucket 不存在是严重错误
            raise RuntimeError(
                f"阿里云 OSS Bucket '{settings.oss_bucket_name}' 不存在，"
                "请在控制台预先创建 Bucket"
            )


def build_oss_key(workspace_id: str, document_id: str, version_id: str, filename: str) -> str:
    """
    构建 OSS 文件 key
    格式：{prefix}/workspaces/{ws_id}/files/{doc_id}/{version_id}/{filename}
    """
    return f"{settings.oss_prefix}/workspaces/{workspace_id}/files/{document_id}/{version_id}/{filename}"


def generate_presign_upload_url(
    oss_key: str,
    content_type: str = "application/octet-stream",
    expires: int = 3600
) -> str:
    """生成预签名上传 URL（PUT 方法，有效期默认 1 小时）"""
    client = get_oss_client()
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.oss_bucket_name,
            "Key": oss_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires,
    )


def generate_presign_download_url(oss_key: str, expires: int = 3600) -> str:
    """生成预签名下载 URL（GET 方法，有效期默认 1 小时）"""
    client = get_oss_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.oss_bucket_name, "Key": oss_key},
        ExpiresIn=expires,
    )


def delete_object(oss_key: str):
    """删除 OSS 文件"""
    client = get_oss_client()
    client.delete_object(Bucket=settings.oss_bucket_name, Key=oss_key)


# ========== 共享收件箱（Shared Inbox）方法 ==========

def _shared_manifest_key(device_id: str) -> str:
    return f"{settings.oss_prefix}/shared/{device_id}/_manifest.json"


def _shared_file_key(device_id: str, doc_id: str, filename: str) -> str:
    return f"{settings.oss_prefix}/shared/{device_id}/{doc_id}/{filename}"


def list_shared_device_ids() -> list:
    """列出 shared/ 下所有设备 ID（子文件夹名）"""
    import json as _json
    client = get_oss_client()
    prefix = f"{settings.oss_prefix}/shared/"
    paginator = client.get_paginator("list_objects_v2")
    device_ids = set()
    try:
        for page in paginator.paginate(
            Bucket=settings.oss_bucket_name,
            Prefix=prefix,
            Delimiter="/",
        ):
            for cp in page.get("CommonPrefixes", []):
                # cp["Prefix"] 形如 "teamhub/shared/{device_id}/"
                part = cp["Prefix"][len(prefix):]  # "{device_id}/"
                dev_id = part.rstrip("/")
                if dev_id:
                    device_ids.add(dev_id)
    except Exception:
        pass
    return list(device_ids)


def get_shared_manifest(device_id: str) -> dict | None:
    """读取 shared/{device_id}/_manifest.json，不存在返回 None"""
    import json as _json
    client = get_oss_client()
    key = _shared_manifest_key(device_id)
    try:
        resp = client.get_object(Bucket=settings.oss_bucket_name, Key=key)
        return _json.loads(resp["Body"].read().decode("utf-8"))
    except Exception:
        return None


def put_shared_manifest(device_id: str, data: dict):
    """写入 shared/{device_id}/_manifest.json"""
    import json as _json
    client = get_oss_client()
    key = _shared_manifest_key(device_id)
    body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
    client.put_object(
        Bucket=settings.oss_bucket_name,
        Key=key,
        Body=body,
        ContentType="application/json",
    )


def upload_shared_file(local_path: str, device_id: str, doc_id: str, filename: str):
    """上传文件到 shared/{device_id}/{doc_id}/{filename}"""
    client = get_oss_client()
    key = _shared_file_key(device_id, doc_id, filename)
    with open(local_path, "rb") as f:
        client.put_object(Bucket=settings.oss_bucket_name, Key=key, Body=f)


def download_shared_file(device_id: str, doc_id: str, filename: str, local_path: str) -> bool:
    """从 OSS 下载共享文件到本地路径，返回是否成功"""
    import os
    client = get_oss_client()
    key = _shared_file_key(device_id, doc_id, filename)
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        client.download_file(settings.oss_bucket_name, key, local_path)
        return True
    except Exception:
        return False


def delete_shared_doc(device_id: str, doc_id: str) -> int:
    """删除 shared/{device_id}/{doc_id}/ 下所有对象，返回删除数量"""
    client = get_oss_client()
    prefix = f"{settings.oss_prefix}/shared/{device_id}/{doc_id}/"
    paginator = client.get_paginator("list_objects_v2")
    keys_to_delete = []
    try:
        for page in paginator.paginate(Bucket=settings.oss_bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys_to_delete.append({"Key": obj["Key"]})
    except Exception:
        return 0

    if not keys_to_delete:
        return 0

    client.delete_objects(
        Bucket=settings.oss_bucket_name,
        Delete={"Objects": keys_to_delete},
    )
    return len(keys_to_delete)

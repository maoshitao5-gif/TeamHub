"""
本地后端 — OSS 客户端封装
支持阿里云 OSS（oss2）和本地 MinIO（boto3/S3），通过 config.json 的 oss_mode 字段切换。

公共接口（OSSClient / MinIOClient 均实现）：
  - upload_shared_file       — 上传文件到 shared/{device}/{doc}/{filename}
  - get_shared_manifest      — 读取设备共享清单
  - put_shared_manifest      — 写入设备共享清单
  - list_shared_device_ids   — 列出 shared/ 下所有设备 ID
  - delete_shared_doc        — 删除设备文档的所有 OSS 文件
  - sign_url_for_download    — 生成预签名下载 URL
  - test_connection          — 连接测试
  - get_oss_client()         — 工厂函数（读 config.json → 创建对应实例）
"""
import json
import os
from pathlib import Path
from typing import Optional

from backend.app.core.logger import get_logger

logger = get_logger("oss_client")


def _fix_proxy_env():
    """
    修复 Windows 上格式错误的代理环境变量（如 http:127.0.0.1:7897 缺少 //）。
    oss2 直接使用 requests，malformed proxy URL 会导致所有请求失败。
    必须在 import oss2 之前调用。
    """
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key, "")
        if val and "://" not in val:
            os.environ.pop(key, None)
            logger.debug(f"已清除格式错误的代理环境变量: {key}={val}")


# 模块加载时立即修复，确保 oss2 import 前代理已清理
_fix_proxy_env()


# ===========================================================================
# 阿里云 OSS 客户端（oss2）
# ===========================================================================

class OSSClient:
    """阿里云 OSS 操作封装（使用 oss2 SDK）"""

    def __init__(self, endpoint: str, key_id: str, key_secret: str,
                 bucket_name: str, prefix: str = "teamhub"):
        try:
            import oss2
        except ImportError:
            raise RuntimeError("oss2 包未安装，请执行 pip install oss2==2.18.4")

        self.prefix = prefix.rstrip("/")
        auth = oss2.Auth(key_id, key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, bucket_name)

    def _key(self, *parts: str) -> str:
        return "/".join([self.prefix] + list(parts))

    def test_connection(self) -> bool:
        try:
            self.bucket.get_bucket_info()
            return True
        except Exception as e:
            logger.warning(f"阿里云 OSS 连接测试失败: {e}")
            return False

    def _put_json(self, key: str, data: dict) -> None:
        content = json.dumps(data, ensure_ascii=False, default=str)
        self.bucket.put_object(key, content.encode("utf-8"))

    def _get_json(self, key: str) -> Optional[dict]:
        try:
            result = self.bucket.get_object(key)
            return json.loads(result.read().decode("utf-8"))
        except Exception as e:
            err = str(e)
            if "NoSuchKey" not in err and "404" not in err:
                logger.warning(f"OSS get_json 失败 {key}: {e}")
            return None

    def upload_shared_file(self, local_path: str, device_id: str, doc_id: str, filename: str) -> str:
        key = self._key("shared", device_id, doc_id, filename)
        self.bucket.put_object_from_file(key, local_path)
        logger.info(f"OSS 共享文件上传成功: {key}")
        return key

    def get_shared_manifest(self, device_id: str) -> Optional[dict]:
        key = self._key("shared", device_id, "_manifest.json")
        return self._get_json(key)

    def put_shared_manifest(self, device_id: str, manifest: dict) -> None:
        key = self._key("shared", device_id, "_manifest.json")
        self._put_json(key, manifest)

    def list_shared_device_ids(self) -> list:
        import oss2
        prefix = self._key("shared") + "/"
        device_ids = set()
        try:
            for obj in oss2.ObjectIterator(self.bucket, prefix=prefix, delimiter="/"):
                if obj.is_prefix():
                    part = obj.key[len(prefix):]
                    dev_id = part.rstrip("/")
                    if dev_id:
                        device_ids.add(dev_id)
        except Exception as e:
            logger.warning(f"OSS list_shared_device_ids 失败: {e}")
        return list(device_ids)

    def delete_shared_doc(self, device_id: str, doc_id: str) -> int:
        import oss2
        prefix = self._key("shared", device_id, doc_id) + "/"
        deleted = 0
        try:
            for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
                try:
                    self.bucket.delete_object(obj.key)
                    deleted += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"OSS delete_shared_doc 失败: {e}")
        return deleted

    def sign_url_for_download(self, device_id: str, doc_id: str, filename: str, expires: int = 3600) -> str:
        key = self._key("shared", device_id, doc_id, filename)
        return self.bucket.sign_url("GET", key, expires)


# ===========================================================================
# MinIO / S3 兼容客户端（boto3，用于本地测试）
# ===========================================================================

class MinIOClient:
    """MinIO（S3 兼容）存储操作封装（使用 boto3 SDK）"""

    def __init__(self, endpoint: str, key_id: str, key_secret: str,
                 bucket_name: str, prefix: str = "teamhub"):
        try:
            import boto3
            from botocore.client import Config
        except ImportError:
            raise RuntimeError("boto3 包未安装，请执行 pip install boto3")

        self.prefix = prefix.rstrip("/")
        self.bucket_name = bucket_name
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=key_secret,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

    def _key(self, *parts: str) -> str:
        return "/".join([self.prefix] + list(parts))

    def test_connection(self) -> bool:
        """测试连接：先 head_bucket，不存在则尝试创建（仅限 MinIO 本地环境）"""
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            return True
        except Exception:
            pass
        # bucket 不存在时尝试自动创建
        try:
            self.client.create_bucket(Bucket=self.bucket_name)
            logger.info(f"MinIO bucket 已创建: {self.bucket_name}")
            return True
        except Exception as e:
            logger.warning(f"MinIO 连接测试失败: {e}")
            return False

    def upload_shared_file(self, local_path: str, device_id: str, doc_id: str, filename: str) -> str:
        key = self._key("shared", device_id, doc_id, filename)
        self.client.upload_file(local_path, self.bucket_name, key)
        logger.info(f"MinIO 共享文件上传成功: {key}")
        return key

    def get_shared_manifest(self, device_id: str) -> Optional[dict]:
        key = self._key("shared", device_id, "_manifest.json")
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return json.loads(resp["Body"].read().decode("utf-8"))
        except Exception as e:
            err = str(e)
            if "NoSuchKey" not in err and "404" not in err:
                logger.warning(f"MinIO get_manifest 失败 {key}: {e}")
            return None

    def put_shared_manifest(self, device_id: str, manifest: dict) -> None:
        key = self._key("shared", device_id, "_manifest.json")
        body = json.dumps(manifest, ensure_ascii=False, default=str).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket_name, Key=key,
            Body=body, ContentType="application/json"
        )

    def list_shared_device_ids(self) -> list:
        prefix = self._key("shared") + "/"
        paginator = self.client.get_paginator("list_objects_v2")
        device_ids = set()
        try:
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix, Delimiter="/"):
                for cp in page.get("CommonPrefixes", []):
                    part = cp["Prefix"][len(prefix):]
                    dev_id = part.rstrip("/")
                    if dev_id:
                        device_ids.add(dev_id)
        except Exception as e:
            logger.warning(f"MinIO list_shared_device_ids 失败: {e}")
        return list(device_ids)

    def delete_shared_doc(self, device_id: str, doc_id: str) -> int:
        prefix = self._key("shared", device_id, doc_id) + "/"
        paginator = self.client.get_paginator("list_objects_v2")
        keys = []
        try:
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append({"Key": obj["Key"]})
        except Exception:
            return 0
        if keys:
            self.client.delete_objects(Bucket=self.bucket_name, Delete={"Objects": keys})
        return len(keys)

    def sign_url_for_download(self, device_id: str, doc_id: str, filename: str, expires: int = 3600) -> str:
        key = self._key("shared", device_id, doc_id, filename)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires,
        )


# ===========================================================================
# 工厂函数：从 config.json 读 oss_mode，创建对应客户端
# ===========================================================================

def get_oss_client():
    """
    从 config.json 读取 oss_mode（默认 "aliyun"），返回对应客户端实例。
    - "aliyun"：用 oss_* 字段创建 OSSClient（oss2）
    - "minio" ：用 minio_* 字段创建 MinIOClient（boto3）
    配置不完整时返回 None。
    """
    try:
        from backend.app.database import load_library_config
        cfg = load_library_config()
        mode = cfg.get("oss_mode", "aliyun")

        if mode == "minio":
            required = ["minio_endpoint", "minio_access_key_id", "minio_access_key_secret", "minio_bucket_name"]
            if not all(cfg.get(k) for k in required):
                return None
            return MinIOClient(
                endpoint=cfg["minio_endpoint"],
                key_id=cfg["minio_access_key_id"],
                key_secret=cfg["minio_access_key_secret"],
                bucket_name=cfg["minio_bucket_name"],
                prefix=cfg.get("minio_prefix", "teamhub"),
            )
        else:  # aliyun（默认）
            required = ["oss_endpoint", "oss_access_key_id", "oss_access_key_secret", "oss_bucket_name"]
            if not all(cfg.get(k) for k in required):
                return None
            return OSSClient(
                endpoint=cfg["oss_endpoint"],
                key_id=cfg["oss_access_key_id"],
                key_secret=cfg["oss_access_key_secret"],
                bucket_name=cfg["oss_bucket_name"],
                prefix=cfg.get("oss_prefix", "teamhub"),
            )
    except Exception as e:
        logger.warning(f"创建 OSS 客户端失败: {e}")
        return None

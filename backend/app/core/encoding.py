"""
编码处理工具模块
处理 Windows GBK 编码环境下的 Unicode 字符问题
"""
import sys
import os
import locale


def setup_utf8_encoding():
    """
    设置全局 UTF-8 编码配置
    这对于 Windows 系统特别重要，因为默认编码可能是 GBK
    """
    if sys.platform == 'win32':
        # Windows 系统：设置控制台编码为 UTF-8
        try:
            # 设置环境变量
            os.environ['PYTHONIOENCODING'] = 'utf-8'
            # 尝试设置控制台编码（如果支持）
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            if hasattr(sys.stderr, 'reconfigure'):
                sys.stderr.reconfigure(encoding='utf-8', errors='replace')
            if hasattr(sys.stdin, 'reconfigure'):
                sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            # 如果设置失败，继续执行（某些环境可能不支持）
            pass

    # 设置默认编码为 UTF-8（Python 3.7+）
    try:
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except locale.Error:
        try:
            locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        except locale.Error:
            # 如果都失败，使用系统默认
            pass


def safe_str(obj) -> str:
    """
    安全地将对象转换为字符串，处理 Unicode 编码错误
    在 Windows GBK 环境下，某些 Unicode 字符无法编码，需要特殊处理
    """
    try:
        s = str(obj)
        # 首先尝试编码为 GBK（Windows 默认编码），如果失败则使用 ASCII 安全版本
        try:
            s.encode('gbk')
            return s
        except UnicodeEncodeError:
            # 如果 GBK 编码失败，使用 ASCII 安全版本（替换无法编码的字符）
            return s.encode('ascii', 'replace').decode('ascii')
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        # 如果转换失败，使用 ASCII 安全版本
        try:
            return str(obj).encode('ascii', 'replace').decode('ascii')
        except:
            return repr(obj)


def safe_print(*args, **kwargs):
    """
    安全的 print 函数，处理 Windows GBK 编码环境下的 Unicode 字符
    """
    try:
        # 将所有参数转换为安全的字符串
        safe_args = []
        for arg in args:
            if isinstance(arg, str):
                # 对于字符串，先尝试安全转换
                safe_arg = safe_str(arg)
                # 确保可以编码为GBK（Windows默认编码）
                try:
                    safe_arg.encode('gbk')
                    safe_args.append(safe_arg)
                except UnicodeEncodeError:
                    # 如果GBK编码失败，使用ASCII安全版本
                    safe_args.append(safe_arg.encode('ascii', 'replace').decode('ascii'))
            else:
                safe_args.append(arg)
        print(*safe_args, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        # 如果仍然失败，使用 repr 输出
        try:
            safe_args = [repr(arg) for arg in args]
            print(*safe_args, **kwargs)
        except:
            # 最后的备选方案：输出错误信息
            try:
                print(f"[编码错误] 无法输出内容: {type(e).__name__}")
            except:
                pass  # 如果连这个都失败，就静默失败

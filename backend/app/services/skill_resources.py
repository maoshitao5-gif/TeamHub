"""Read only named methods bundled with TeamHub, never caller-provided paths."""
from pathlib import Path
from typing import Literal

METHOD_VERSION = '1'
Method = Literal['text', 'tables', 'visuals', 'mixed']
REFERENCE_ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'document-understanding' / 'references'
METHODS = {
    'text': ('text.md', '文字、长文、条款与章节结构'),
    'tables': ('tables.md', '明细数据、填报表、多区域表格与工作簿'),
    'visuals': ('visuals.md', '扫描页、图纸、图片与可视化图表'),
    'mixed': ('mixed.md', '正文与表格、图像交叉表达的混合资料'),
}


def read_reference(method: str) -> dict:
    if method not in METHODS:
        raise ValueError('请选择当前提供的阅读方法。')
    filename, applies_to = METHODS[method]
    candidate = REFERENCE_ROOT / filename
    if candidate.is_symlink() or candidate.resolve().parent != REFERENCE_ROOT.resolve():
        raise ValueError('阅读方法资源无效，请重新安装应用。')
    try:
        # Bundled references are deliberately small; a corrupted package cannot
        # inject an unbounded resource into the model context.
        with candidate.open('r', encoding='utf-8') as stream:
            content = stream.read(16001)
        if not content.strip() or len(content) > 16000:
            raise ValueError('阅读方法资源无效，请重新安装应用。')
    except (OSError, UnicodeError):
        raise ValueError('阅读方法资源不可用，请重新安装应用。') from None
    return {'ok': True, 'method': method, 'version': METHOD_VERSION,
            'applies_to': applies_to, 'content': content}

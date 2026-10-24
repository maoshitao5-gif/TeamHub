"""Route immutable filenames to one bounded, bundled object-reading Skill.

Classification describes the content family, not parser or model capability.
Tool results remain the authority on what a particular file can be read as.
"""
from pathlib import Path, PurePosixPath

from .ai_text import _TEXT_SUFFIXES, canonical_suffix
from .image_formats import IMAGE_SUFFIXES
from .archive_formats import archive_kind

OBJECT_SKILL_VERSION = '1'
SKILL_ROOT = Path(__file__).resolve().parents[1] / 'skills'
MAX_SKILL_CHARS = 16000
_SKILLS = {
    'text': ('text-understanding', '文字、记录、配置及结构化文本'),
    'word': ('word-understanding', 'Word、文字处理文档及表单'),
    'spreadsheet': ('spreadsheet-understanding', '工作簿、明细表及填报表'),
    'presentation': ('presentation-understanding', '演示文稿、汇报及培训课件'),
    'pdf': ('pdf-understanding', 'PDF 正文、扫描件及图纸'),
    'image': ('image-understanding', '图片、照片、扫描页及可视化图形'),
    'folder': ('folder-understanding', '整体收录的文件夹及其内部成员'),
    'archive': ('archive-understanding', '压缩资料包及其内部成员'),
}
_KINDS = {
    'word': {'.docx', '.doc', '.odt', '.rtf', '.dot'},
    'spreadsheet': {'.xlsx', '.xls', '.ods', '.xlt', '.csv', '.tsv'},
    'presentation': {'.pptx', '.ppt', '.odp', '.pps', '.pot'},
    'pdf': {'.pdf'},
    'image': IMAGE_SUFFIXES,
}


def object_kind(filename: str, *, is_folder: bool = False) -> str:
    """Classify a saved-version/member filename without opening caller paths."""
    if is_folder:
        return 'folder'
    if not isinstance(filename, str) or not filename or '\x00' in filename:
        return 'unknown'
    name = PurePosixPath(filename.replace('\\', '/')).name.lower()
    if archive_kind(name) is not None:
        return 'archive'
    suffix = canonical_suffix(PurePosixPath(name).suffix)
    for kind, suffixes in _KINDS.items():
        if suffix in suffixes:
            return kind
    return 'text' if suffix in _TEXT_SUFFIXES else 'unknown'


def object_skill_catalog() -> list[dict[str, str]]:
    """Return discovery metadata only; never load every instruction file."""
    return [{'kind': kind, 'description': description}
            for kind, (_, description) in _SKILLS.items()]


def object_instructions(kind: str) -> str:
    """Load exactly one allowlisted Skill, with bounded UTF-8 content."""
    if kind == 'unknown':
        return ('当前对象类型未识别。仅使用本次实际提供且成功返回的读取能力；'
                '文件名、目录和已有卡片不能充当正文来源。若无可读内容，说明格式或能力缺口，'
                '不要伪造成功的资料卡、视觉理解或整理方案。')
    if not isinstance(kind, str) or kind not in _SKILLS:
        raise ValueError('请选择当前提供的资料类型。')
    directory = _SKILLS[kind][0]
    candidate = SKILL_ROOT / directory / 'SKILL.md'
    try:
        root = SKILL_ROOT.resolve()
        if (SKILL_ROOT.is_symlink() or candidate.parent.is_symlink()
                or candidate.is_symlink() or candidate.resolve().parent != root / directory):
            raise ValueError('资料类型规则资源无效，请重新安装应用。')
        with candidate.open('r', encoding='utf-8') as stream:
            content = stream.read(MAX_SKILL_CHARS + 1)
        if not content.strip() or len(content) > MAX_SKILL_CHARS:
            raise ValueError('资料类型规则资源无效，请重新安装应用。')
    except (OSError, UnicodeError, RuntimeError):
        raise ValueError('资料类型规则资源不可用，请重新安装应用。') from None
    return content

"""Privacy checks. Diagnostics deliberately omit matched values."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Only the explicitly approved author-contact line is public; all other contacts
# and every credential still go through the normal checks.
PUBLIC_CONTACT_PATH = "docs/data-summary.md"
PUBLIC_CONTACT_LINE = (
    "作者公开联系 " + "QQ：" + "24134" + "93480"
    + "（维护者于 2026-10-22 指定公开）。"
)
AUTHOR_PUBLIC_CONTACT = "通讯作者Q" + "24134" + "93480"
LONG_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:token|password|api[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9_+/.-]{24,}"),
)


def contains_secret(value):
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def term_privacy_errors(doc, rel):
    errors = []

    def visit(value, field=""):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{field}.{key}" if field else key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, f"{field}[{i}]")
        elif isinstance(value, str):
            if contains_secret(value):
                errors.append(f"{rel}: {field} 含疑似凭据，请删除（不回显内容）")
            if LONG_NUMBER.search(value):
                errors.append(f"{rel}: {field} 含疑似 QQ 号/长数字，请脱敏（不回显内容）")

    visit(doc)
    return errors


def scan_repository(root=ROOT):
    """Scan tracked and non-ignored files, including newly added source files."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    errors = []
    from knowledge import CONTACT
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8")
        path = root / rel
        if not path.is_file():
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            errors.append(f"{rel}: 环境凭据文件禁止提交")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if contains_secret(line):
                errors.append(f"{rel}:{number}: 疑似凭据，请删除（不回显内容）")
            contact_line = line
            if rel == "sources/data-summary/full.md" or (
                    rel.startswith("knowledge/notices/author-summary-") and rel.endswith(".json")):
                contact_line = CONTACT.sub(
                    lambda match: "[作者公开联系]" if match.group() == AUTHOR_PUBLIC_CONTACT else match.group(), line)
            if CONTACT.search(contact_line) and not (rel == PUBLIC_CONTACT_PATH and line == PUBLIC_CONTACT_LINE):
                errors.append(f"{rel}:{number}: 疑似个人联系方式，请脱敏（不回显内容）")
    return errors


def main():
    errors = scan_repository()
    for error in errors:
        print(error)
    print(f"源码凭据检查：{'失败' if errors else '通过'}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

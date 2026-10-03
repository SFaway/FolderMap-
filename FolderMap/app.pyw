from __future__ import annotations

import json
import os
import threading
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from tkinter import Tk, Toplevel, Frame, Label, Entry, Button, StringVar, BooleanVar, Checkbutton, Radiobutton, filedialog, messagebox, ttk
from xml.sax.saxutils import escape

APP_NAME = "FolderMap"
APP_SUBTITLE = "扫描本地文件夹，生成可直接发给 AI 的文件目录与文件信息"

COLORS = {
    "bg": "#F5FAF8",
    "card": "#FFFFFF",
    "primary": "#55B89E",
    "primary_hover": "#419B83",
    "primary_soft": "#EAF7F3",
    "text": "#263633",
    "muted": "#74847F",
    "helper": "#5E716B",
    "line": "#DDE9E5",
    "field": "#F8FBFA",
}

DEFAULT_IGNORE_DIRS = [
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "build", "dist", ".idea", ".vscode"
]

COMMON_IGNORE_DIRS = [
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "build", "dist", ".idea", ".vscode", "target", "out",
    "coverage", ".cache"
]

DEFAULT_IGNORE_SUFFIXES: list[str] = []

COMMON_IGNORE_SUFFIXES = [
    ".c", ".cpp", ".h", ".hpp",
    ".rar", ".zip", ".7z",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png", ".gif", ".svg",
    ".mp3", ".wav", ".mp4", ".mov",
    ".exe", ".dll", ".log", ".tmp", ".cache", ".pyc", "无后缀"
]


@dataclass
class FileItem:
    name: str
    suffix: str
    rel_path: str
    full_path: str
    size: int
    modified: float


@dataclass
class FolderNode:
    name: str
    rel_path: str
    folders: list["FolderNode"] = field(default_factory=list)
    files: list[FileItem] = field(default_factory=list)


@dataclass
class ScanResult:
    root: str
    root_name: str
    scanned_at: str
    folder_count: int
    file_count: int
    extension_counts: dict[str, int]
    tree: FolderNode


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def scan_folder(root_path: str, ignore_names: set[str], ignore_suffixes: set[str]) -> ScanResult:
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("请选择有效的文件夹。")

    ignore_name_lower: set[str] = set()
    ignore_rel_lower: set[str] = set()
    for raw in ignore_names:
        token = raw.strip().replace("\\", "/")
        if not token:
            continue
        if token.startswith("./"):
            ignore_rel_lower.add(token[2:].strip("/").lower())
        elif "/" in token:
            ignore_rel_lower.add(token.strip("/").lower())
        else:
            ignore_name_lower.add(token.lower())
    ignore_suffixes_lower = {x.lower() for x in ignore_suffixes}

    folder_count = 0
    file_count = 0
    ext_counts: dict[str, int] = {}

    def build(path: Path, rel: str) -> FolderNode:
        nonlocal folder_count, file_count
        node = FolderNode(path.name or str(path), rel)
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return node

        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    child_rel = str(Path(rel) / entry.name) if rel else entry.name
                    child_rel_norm = child_rel.replace("\\", "/").strip("/").lower()
                    if entry.name.lower() in ignore_name_lower or child_rel_norm in ignore_rel_lower:
                        continue
                    folder_count += 1
                    node.folders.append(build(entry, child_rel))
                elif entry.is_file():
                    suffix = entry.suffix.lower() or "无后缀"
                    if suffix.lower() in ignore_suffixes_lower:
                        continue
                    st = entry.stat()
                    rel_path = str(Path(rel) / entry.name) if rel else entry.name
                    node.files.append(FileItem(
                        name=entry.name,
                        suffix=suffix,
                        rel_path=rel_path,
                        full_path=str(entry),
                        size=st.st_size,
                        modified=st.st_mtime,
                    ))
                    file_count += 1
                    ext_counts[suffix] = ext_counts.get(suffix, 0) + 1
            except (PermissionError, OSError):
                continue
        return node

    tree = build(root, "")
    return ScanResult(
        root=str(root),
        root_name=root.name or str(root),
        scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        folder_count=folder_count,
        file_count=file_count,
        extension_counts=dict(sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        tree=tree,
    )


def describe_file(item: FileItem, detailed: bool, include_path: bool, include_size: bool, include_modified: bool) -> str:
    # “简洁 / 详细”只是快捷预设；真正决定导出内容的是下面三个勾选项。
    # 因此即使处于“简洁”模式，用户手动勾选某项后也会正常导出。
    parts = [item.name]
    if include_path:
        parts.append(f"路径: {item.full_path}")
    if include_size:
        parts.append(f"大小: {format_size(item.size)}")
    if include_modified:
        parts.append(f"修改: {format_time(item.modified)}")
    return " | ".join(parts)


def folder_label(node: FolderNode) -> str:
    file_text = f"{len(node.files)} 个文件"
    folder_text = f"{len(node.folders)} 个子文件夹"
    return f"【文件夹】{node.name}  （本层 {file_text} · {folder_text}）"


def build_txt(result: ScanResult, detailed=False, include_path=False, include_size=False, include_modified=False) -> str:
    lines = [
        f"文件目录信息：{result.root_name}",
        f"根目录：{result.root}",
        f"扫描时间：{result.scanned_at}",
        f"共 {result.folder_count} 个子文件夹 · {result.file_count} 个文件",
        "=" * 64,
        "",
        folder_label(result.tree),
    ]

    def walk(node: FolderNode, prefix: str = ""):
        entries = [("folder", x) for x in node.folders] + [("file", x) for x in node.files]
        for i, (kind, obj) in enumerate(entries):
            is_last = i == len(entries) - 1
            branch = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")
            if kind == "folder":
                lines.append(prefix + branch + folder_label(obj))
                walk(obj, next_prefix)
            else:
                lines.append(prefix + branch + describe_file(obj, detailed, include_path, include_size, include_modified))

    walk(result.tree)
    return "\n".join(lines).rstrip() + "\n"


def build_markdown(result: ScanResult, detailed=False, include_path=False, include_size=False, include_modified=False) -> str:
    lines = [
        f"# 文件目录信息：{result.root_name}", "",
        f"> 根目录：`{result.root}`  ",
        f"> 扫描时间：{result.scanned_at}  ",
        f"> 共 {result.folder_count} 个子文件夹 ｜ {result.file_count} 个文件", "",
        f"## {folder_label(result.tree)}", "",
    ]

    def walk(node: FolderNode, depth: int = 3, is_root: bool = False):
        if not is_root:
            lines.append(f"{'#' * min(depth, 6)} {folder_label(node)}")
            lines.append("")
        for item in node.files:
            lines.append("- " + describe_file(item, detailed, include_path, include_size, include_modified))
        if node.files:
            lines.append("")
        for folder in node.folders:
            walk(folder, depth + 1)

    walk(result.tree, depth=2, is_root=True)
    return "\n".join(lines).rstrip() + "\n"


def build_json(result: ScanResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2)


def _docx_paragraph(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    safe = escape(text)
    return (
        '<w:p>' + ppr +
        '<w:r><w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/></w:rPr>'
        f'<w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
    )


def _docx_folder_paragraph(node: FolderNode, level: int) -> str:
    style = f"Heading{min(level, 3)}"
    left = max(0, (level - 1) * 280)
    safe = escape(folder_label(node))
    return (
        '<w:p>'
        f'<w:pPr><w:pStyle w:val="{style}"/><w:ind w:left="{left}"/>'
        '<w:shd w:val="clear" w:color="auto" w:fill="EAF7F3"/>'
        '<w:spacing w:before="160" w:after="70"/></w:pPr>'
        '<w:r><w:rPr><w:b/><w:rFonts w:eastAsia="Microsoft YaHei"/></w:rPr>'
        f'<w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
    )


def _docx_file_paragraph(text: str, level: int) -> str:
    left = max(360, level * 360)
    safe = escape("• " + text)
    return (
        '<w:p><w:pPr><w:ind w:left="' + str(left) + '"/><w:spacing w:after="35"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/></w:rPr>'
        f'<w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
    )


def export_docx(result: ScanResult, output_path: str, detailed=False, include_path=False, include_size=False, include_modified=False):
    paragraphs = [
        _docx_paragraph(f"文件目录信息｜{result.root_name}", "Title"),
        _docx_paragraph(f"根目录：{result.root}"),
        _docx_paragraph(f"扫描时间：{result.scanned_at}"),
        _docx_paragraph(f"共 {result.folder_count} 个子文件夹 ｜ {result.file_count} 个文件"),
        _docx_paragraph(""),
        _docx_folder_paragraph(result.tree, 1),
    ]

    def walk(node: FolderNode, level: int = 1, is_root: bool = False):
        if not is_root:
            paragraphs.append(_docx_folder_paragraph(node, level))
        for item in node.files:
            paragraphs.append(_docx_file_paragraph(describe_file(item, detailed, include_path, include_size, include_modified), level))
        for folder in node.folders:
            walk(folder, level + 1)

    walk(result.tree, 1, True)

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {''.join(paragraphs)}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'''

    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
</w:styles>'''

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)


class FolderMapApp(Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1060x720")
        self.minsize(960, 700)
        self.configure(bg=COLORS["bg"])
        self.result: ScanResult | None = None

        self.path_var = StringVar()
        self.ignore_dir_var = StringVar(value=", ".join(DEFAULT_IGNORE_DIRS))
        self.ignore_suffix_var = StringVar(value=", ".join(DEFAULT_IGNORE_SUFFIXES))
        self.common_dir_var = StringVar(value="常见目录")
        self.common_suffix_var = StringVar(value="常见后缀")
        self.mode_var = StringVar(value="simple")
        self.format_var = StringVar(value="Word (.docx)")
        self.path_opt = BooleanVar(value=False)
        self.size_opt = BooleanVar(value=False)
        self.time_opt = BooleanVar(value=False)
        self.stats_var = StringVar(value="尚未扫描")
        self.status_var = StringVar(value="选择一个文件夹开始")

        self._setup_style()
        self._build_ui()

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=COLORS["card"], fieldbackground=COLORS["card"], foreground=COLORS["text"], rowheight=28, borderwidth=0, font=("Segoe UI", 10))
        style.map("Treeview", background=[("selected", COLORS["primary_soft"])], foreground=[("selected", COLORS["text"])])
        style.configure(
            "Mint.TCombobox",
            padding=6,
            fieldbackground=COLORS["primary_soft"],
            background=COLORS["primary_soft"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["primary_hover"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
        )
        style.map(
            "Mint.TCombobox",
            fieldbackground=[("readonly", COLORS["primary_soft"])],
            background=[("readonly", COLORS["primary_soft"])],
            foreground=[("readonly", COLORS["text"])],
            selectbackground=[("readonly", COLORS["primary_soft"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        self.option_add("*TCombobox*Listbox.background", COLORS["card"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", COLORS["primary_soft"])
        self.option_add("*TCombobox*Listbox.selectForeground", COLORS["text"])

    def _card(self, row: int, expand: bool = False):
        outer = Frame(self.content, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["line"])
        outer.grid(row=row, column=0, sticky="nsew" if expand else "ew", padx=24, pady=(0, 10))
        inner = Frame(outer, bg=COLORS["card"])
        inner.pack(fill="both", expand=True, padx=20, pady=12)
        return inner

    def _button(self, parent, text, command, secondary=False, big=False):
        bg = COLORS["primary_soft"] if secondary else COLORS["primary"]
        fg = COLORS["primary_hover"] if secondary else "#FFFFFF"
        return Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=COLORS["primary_hover"], activeforeground="#FFFFFF", relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 10 if big else 9, "bold"), padx=24 if big else 18, pady=12 if big else 9)

    def _build_ui(self):
        self.content = Frame(self, bg=COLORS["bg"])
        self.content.pack(fill="both", expand=True)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(2, weight=1)

        head = Frame(self.content, bg=COLORS["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 12))
        Label(head, text=APP_NAME, bg=COLORS["bg"], fg=COLORS["text"], font=("Segoe UI", 24, "bold")).pack(anchor="w")
        Label(head, text=APP_SUBTITLE, bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))

        scan = self._card(1)
        Label(scan, text="1  选择并扫描文件夹", bg=COLORS["card"], fg=COLORS["text"], font=("Segoe UI", 11, "bold")).pack(anchor="w")
        row = Frame(scan, bg=COLORS["card"])
        row.pack(fill="x", pady=(9, 8))
        Entry(row, textvariable=self.path_var, bg=COLORS["field"], fg=COLORS["text"], relief="flat", highlightthickness=1, highlightbackground=COLORS["line"], highlightcolor=COLORS["primary"], font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=9, padx=(0, 10))
        self._button(row, "选择文件夹", self.choose_folder, secondary=True).pack(side="left")
        self.scan_btn = self._button(row, "开始扫描", self.start_scan)
        self.scan_btn.pack(side="left", padx=(10, 0))
        self.export_btn = self._button(row, "导出文件", self.export_file, big=True)
        self.export_btn.configure(state="disabled")
        self.export_btn.pack(side="left", padx=(10, 0))

        filter_title = Frame(scan, bg=COLORS["card"])
        filter_title.pack(fill="x", pady=(0, 4))
        Label(filter_title, text="过滤规则", bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(side="left")
        Label(filter_title, text="逗号分隔  ·  常见项可快捷添加  ·  也可从当前扫描目录多选文件夹", bg=COLORS["card"], fg=COLORS["helper"], font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(10, 0))

        ignore_dir_row = Frame(scan, bg=COLORS["card"])
        ignore_dir_row.pack(fill="x", pady=(0, 5))
        Label(ignore_dir_row, text="忽略目录", width=9, anchor="w", bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="left")
        Entry(ignore_dir_row, textvariable=self.ignore_dir_var, bg=COLORS["primary_soft"], fg=COLORS["text"], relief="flat", highlightthickness=0, font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        self.dir_combo = ttk.Combobox(
            ignore_dir_row, textvariable=self.common_dir_var, state="readonly", width=22,
            values=["常见目录"] + COMMON_IGNORE_DIRS, style="Mint.TCombobox"
        )
        self.dir_combo.pack(side="left")
        self.dir_combo.bind("<<ComboboxSelected>>", self.on_common_dir_selected)
        self._button(ignore_dir_row, "加入忽略", self.open_ignore_folder_picker, secondary=True).pack(side="left", padx=(7, 0))

        ignore_suffix_row = Frame(scan, bg=COLORS["card"])
        ignore_suffix_row.pack(fill="x")
        Label(ignore_suffix_row, text="忽略后缀", width=9, anchor="w", bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="left")
        Entry(ignore_suffix_row, textvariable=self.ignore_suffix_var, bg=COLORS["primary_soft"], fg=COLORS["text"], relief="flat", highlightthickness=0, font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        self.suffix_combo = ttk.Combobox(
            ignore_suffix_row, textvariable=self.common_suffix_var, state="readonly", width=22,
            values=["常见后缀"] + COMMON_IGNORE_SUFFIXES, style="Mint.TCombobox"
        )
        self.suffix_combo.pack(side="left")
        self.suffix_combo.bind("<<ComboboxSelected>>", self.on_common_suffix_selected)

        preview = self._card(2, expand=True)
        preview_head = Frame(preview, bg=COLORS["card"])
        preview_head.pack(fill="x", pady=(0, 8))
        Label(preview_head, text="2  目录预览", bg=COLORS["card"], fg=COLORS["text"], font=("Segoe UI", 11, "bold")).pack(side="left")
        Label(preview_head, textvariable=self.stats_var, bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="right")

        tree_wrap = Frame(preview, bg=COLORS["card"])
        tree_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_wrap, show="tree")
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        export = self._card(3)
        top = Frame(export, bg=COLORS["card"])
        top.pack(fill="x")
        Label(top, text="3  导出设置", bg=COLORS["card"], fg=COLORS["text"], font=("Segoe UI", 11, "bold")).pack(side="left")
        Label(top, textvariable=self.status_var, bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="right")

        opts = Frame(export, bg=COLORS["card"])
        opts.pack(fill="x", pady=(10, 8))
        Radiobutton(opts, text="简洁", variable=self.mode_var, value="simple", command=lambda: self.apply_mode_preset("simple"), bg=COLORS["card"], fg=COLORS["text"], activebackground=COLORS["card"], selectcolor=COLORS["primary_soft"], font=("Segoe UI", 9)).pack(side="left")
        Radiobutton(opts, text="详细", variable=self.mode_var, value="full", command=lambda: self.apply_mode_preset("full"), bg=COLORS["card"], fg=COLORS["text"], activebackground=COLORS["card"], selectcolor=COLORS["primary_soft"], font=("Segoe UI", 9)).pack(side="left", padx=(6, 18))
        Checkbutton(opts, text="完整路径", variable=self.path_opt, bg=COLORS["card"], fg=COLORS["text"], activebackground=COLORS["card"], selectcolor=COLORS["primary_soft"], font=("Segoe UI", 9)).pack(side="left")
        Checkbutton(opts, text="文件大小", variable=self.size_opt, bg=COLORS["card"], fg=COLORS["text"], activebackground=COLORS["card"], selectcolor=COLORS["primary_soft"], font=("Segoe UI", 9)).pack(side="left")
        Checkbutton(opts, text="修改时间", variable=self.time_opt, bg=COLORS["card"], fg=COLORS["text"], activebackground=COLORS["card"], selectcolor=COLORS["primary_soft"], font=("Segoe UI", 9)).pack(side="left")

        bottom = Frame(export, bg=COLORS["card"])
        bottom.pack(fill="x")
        Label(bottom, text="导出格式", bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="left", padx=(0, 10))
        ttk.Combobox(
            bottom, textvariable=self.format_var, state="readonly", width=20,
            values=["Word (.docx)", "Markdown (.md)", "TXT (.txt)", "JSON (.json)"],
            style="Mint.TCombobox"
        ).pack(side="left")
    @staticmethod
    def _split_csv(value: str) -> list[str]:
        return [x.strip() for x in value.replace("；", ",").replace("，", ",").split(",") if x.strip()]

    @staticmethod
    def _normalize_suffix(value: str) -> str:
        value = value.strip().lower()
        if value.startswith("*."):
            value = value[1:]
        if value and value != "无后缀" and not value.startswith("."):
            value = "." + value
        return value

    def _append_unique(self, variable: StringVar, value: str, normalize=None):
        value = value.strip()
        if not value or value.startswith("选择常见"):
            return
        if normalize:
            value = normalize(value)
        items = self._split_csv(variable.get())
        compare = {x.lower() for x in items}
        if value.lower() not in compare:
            items.append(value)
            variable.set(", ".join(items))

    def on_common_dir_selected(self, _event=None):
        value = self.common_dir_var.get().strip()
        if not value or value == "常见目录":
            return
        self._append_unique(self.ignore_dir_var, value)
        self.common_dir_var.set("常见目录")

    def _add_custom_ignore_paths(self, relative_paths: list[str]):
        """把用户明确选择的目录作为相对路径加入忽略，避免同名目录被全部过滤。"""
        for rel in relative_paths:
            rel = rel.replace("\\", "/").strip("/")
            if rel:
                self._append_unique(self.ignore_dir_var, "./" + rel)

    def open_ignore_folder_picker(self):
        root_text = self.path_var.get().strip()
        if not root_text:
            messagebox.showinfo(APP_NAME, "请先选择要扫描的文件夹。")
            return
        root_path = Path(root_text)
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showinfo(APP_NAME, "当前扫描目录无效，请重新选择。")
            return

        dialog = Toplevel(self)
        dialog.title("选择要忽略的文件夹")
        dialog.geometry("620x500")
        dialog.minsize(520, 420)
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        head = Frame(dialog, bg=COLORS["bg"])
        head.pack(fill="x", padx=22, pady=(18, 10))
        Label(head, text="选择要忽略的文件夹", bg=COLORS["bg"], fg=COLORS["text"], font=("Segoe UI", 15, "bold")).pack(anchor="w")
        Label(head, text="可按 Ctrl / Shift 多选；这里只显示当前扫描目录中的文件夹。", bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

        card = Frame(dialog, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["line"])
        card.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        wrap = Frame(card, bg=COLORS["card"])
        wrap.pack(fill="both", expand=True, padx=12, pady=12)

        tree = ttk.Treeview(wrap, show="tree", selectmode="extended")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        item_rel: dict[str, str] = {}
        loaded: set[str] = set()

        def add_children(parent_item: str, abs_path: Path, rel_prefix: str):
            if parent_item in loaded:
                return
            loaded.add(parent_item)
            try:
                dirs = sorted(
                    [x for x in abs_path.iterdir() if x.is_dir() and not x.is_symlink()],
                    key=lambda x: x.name.lower(),
                )
            except (OSError, PermissionError):
                return
            for child in dirs:
                rel = f"{rel_prefix}/{child.name}" if rel_prefix else child.name
                iid = tree.insert(parent_item, "end", text=f"📁 {child.name}", open=False)
                item_rel[iid] = rel
                # 只探测是否存在子目录，用占位项支持懒加载。
                try:
                    has_child_dir = any(x.is_dir() and not x.is_symlink() for x in child.iterdir())
                except (OSError, PermissionError):
                    has_child_dir = False
                if has_child_dir:
                    tree.insert(iid, "end", text="…")

        root_item = tree.insert("", "end", text=f"📁 {root_path.name or str(root_path)}", open=True)
        item_rel[root_item] = ""
        add_children(root_item, root_path, "")

        def on_open(_event=None):
            iid = tree.focus()
            if not iid or iid == root_item or iid in loaded:
                return
            rel = item_rel.get(iid, "")
            abs_path = root_path / Path(rel)
            children = tree.get_children(iid)
            if len(children) == 1 and tree.item(children[0], "text") == "…":
                tree.delete(children[0])
            add_children(iid, abs_path, rel)

        tree.bind("<<TreeviewOpen>>", on_open)

        foot = Frame(dialog, bg=COLORS["bg"])
        foot.pack(fill="x", padx=22, pady=(0, 18))

        def confirm():
            selected = []
            for iid in tree.selection():
                rel = item_rel.get(iid, "")
                if rel:
                    selected.append(rel)
            if not selected:
                messagebox.showinfo(APP_NAME, "请至少选择一个要忽略的文件夹。", parent=dialog)
                return
            self._add_custom_ignore_paths(selected)
            dialog.destroy()

        self._button(foot, "取消", dialog.destroy, secondary=True).pack(side="right")
        self._button(foot, "加入所选", confirm).pack(side="right", padx=(0, 8))

    def on_common_suffix_selected(self, _event=None):
        value = self.common_suffix_var.get().strip()
        if not value or value == "常见后缀":
            return
        self._append_unique(self.ignore_suffix_var, value, self._normalize_suffix)
        self.common_suffix_var.set("常见后缀")

    def apply_mode_preset(self, mode: str):
        """简洁/详细只作为快捷预设，之后仍可手动修改三个勾选项。"""
        checked = mode == "full"
        self.path_opt.set(checked)
        self.size_opt.set(checked)
        self.time_opt.set(checked)

    def choose_folder(self):
        selected = filedialog.askdirectory(title="选择要扫描的文件夹")
        if selected:
            self.path_var.set(selected)

    def start_scan(self):
        path = self.path_var.get().strip()
        if not path:
            messagebox.showinfo(APP_NAME, "请先选择一个文件夹。")
            return
        ignores = {x for x in self._split_csv(self.ignore_dir_var.get())}
        ignore_suffixes = {self._normalize_suffix(x) for x in self._split_csv(self.ignore_suffix_var.get())}
        ignore_suffixes.discard("")
        self.scan_btn.configure(state="disabled", text="扫描中…")
        self.export_btn.configure(state="disabled")
        self.status_var.set("正在扫描")
        self.stats_var.set("扫描中…")
        self.tree.delete(*self.tree.get_children())

        def worker():
            try:
                result = scan_folder(path, ignores, ignore_suffixes)
                self.after(0, lambda: self._scan_done(result))
            except Exception as exc:
                self.after(0, lambda: self._scan_failed(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self, result: ScanResult):
        self.result = result
        self.scan_btn.configure(state="normal", text="重新扫描")
        self.export_btn.configure(state="normal")
        self.stats_var.set(f"{result.folder_count} 个文件夹 · {result.file_count} 个文件")
        self.status_var.set("扫描完成，点击上方「导出文件」")
        root = self.tree.insert("", "end", text=f"📁 {result.root_name}", open=True)
        self._insert_node(root, result.tree)

    def _scan_failed(self, message: str):
        self.scan_btn.configure(state="normal", text="开始扫描")
        self.export_btn.configure(state="disabled")
        self.stats_var.set("扫描失败")
        self.status_var.set("扫描失败")
        messagebox.showerror(APP_NAME, message)

    def _insert_node(self, parent, node: FolderNode):
        for folder in node.folders:
            fid = self.tree.insert(parent, "end", text=f"📁 {folder.name}", open=False)
            self._insert_node(fid, folder)
        for item in node.files:
            self.tree.insert(parent, "end", text=f"   {item.name}")

    def export_file(self):
        if not self.result:
            messagebox.showinfo(APP_NAME, "请先扫描一个文件夹。")
            return

        fmt = self.format_var.get()
        ext = {
            "Markdown (.md)": ".md",
            "TXT (.txt)": ".txt",
            "Word (.docx)": ".docx",
            "JSON (.json)": ".json",
        }[fmt]

        output = filedialog.asksaveasfilename(
            title="保存导出文件",
            initialfile=f"{self.result.root_name}_文件目录信息{ext}",
            defaultextension=ext,
            filetypes=[(fmt, f"*{ext}"), ("所有文件", "*.*")],
        )
        if not output:
            return

        detailed = self.mode_var.get() == "full"
        args = dict(
            detailed=detailed,
            include_path=self.path_opt.get(),
            include_size=self.size_opt.get(),
            include_modified=self.time_opt.get(),
        )

        try:
            if ext == ".md":
                Path(output).write_text(build_markdown(self.result, **args), encoding="utf-8-sig")
            elif ext == ".txt":
                Path(output).write_text(build_txt(self.result, **args), encoding="utf-8-sig")
            elif ext == ".json":
                Path(output).write_text(build_json(self.result), encoding="utf-8-sig")
            else:
                export_docx(self.result, output, **args)

            if not Path(output).exists() or Path(output).stat().st_size == 0:
                raise RuntimeError("文件没有成功写入磁盘。")

            self.status_var.set(f"已导出：{Path(output).name}")
            messagebox.showinfo(APP_NAME, f"导出完成\n\n{output}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"导出失败：\n{exc}")


if __name__ == "__main__":
    FolderMapApp().mainloop()

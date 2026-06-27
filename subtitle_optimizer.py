from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

TIME_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
SENTENCE_END_RE = re.compile(r"[.!?…]['\")\]]*$")
SOFT_SPLIT_CHARS = [", ", "; ", ": ", " – ", " - ", " und ", " aber ", " oder ", " denn "]


@dataclass
class Subtitle:
    start: str
    end: str
    text: str


def normalize_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def read_srt(path: Path) -> List[Subtitle]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.strip())
    subtitles: List[Subtitle] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        time_index = None
        match = None
        for i, line in enumerate(lines):
            match = TIME_RE.match(line)
            if match:
                time_index = i
                break

        if time_index is None or match is None:
            continue

        text = normalize_text(" ".join(lines[time_index + 1 :]))
        if not text:
            continue

        subtitles.append(Subtitle(match.group("start"), match.group("end"), text))

    return subtitles


def split_long_text(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]

    parts: List[str] = []
    remaining = text.strip()

    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        best = -1

        for sep in SOFT_SPLIT_CHARS:
            pos = window.rfind(sep)
            if pos > best:
                best = pos + len(sep.rstrip())

        if best < max(40, max_chars // 3):
            best = window.rfind(" ")

        if best < max(40, max_chars // 3):
            best = max_chars

        part = remaining[:best].strip(" ,;:-–")
        if part:
            parts.append(part)
        remaining = remaining[best:].strip()

    if remaining:
        parts.append(remaining)
    return parts


def merge_to_sentences(subtitles: List[Subtitle], max_chars: int = 120) -> List[Subtitle]:
    merged: List[Subtitle] = []
    current_text: List[str] = []
    current_start = ""
    current_end = ""

    def flush() -> None:
        nonlocal current_text, current_start, current_end
        text = normalize_text(" ".join(current_text))
        if not text:
            current_text = []
            return

        chunks = split_long_text(text, max_chars)
        for chunk in chunks:
            merged.append(Subtitle(current_start, current_end, chunk))

        current_text = []
        current_start = ""
        current_end = ""

    for sub in subtitles:
        if not current_text:
            current_start = sub.start
        current_text.append(sub.text)
        current_end = sub.end

        combined = normalize_text(" ".join(current_text))
        should_end = bool(SENTENCE_END_RE.search(combined))
        too_long = len(combined) >= max_chars

        if should_end or too_long:
            flush()

    flush()
    return merged


def write_srt(subtitles: List[Subtitle], path: Path) -> None:
    lines: List[str] = []
    for idx, sub in enumerate(subtitles, start=1):
        lines.append(str(idx))
        lines.append(f"{sub.start} --> {sub.end}")
        lines.append(sub.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def optimize_file(path: Path, max_chars: int = 120) -> Path:
    source = read_srt(path)
    if not source:
        raise ValueError("No valid subtitles were found in this file.")

    cleaned = merge_to_sentences(source, max_chars=max_chars)
    output = path.with_name(f"{path.stem}_clean.srt")
    write_srt(cleaned, output)
    return output


class DropListWidget(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(180)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".srt":
                existing = [self.item(i).text() for i in range(self.count())]
                if str(path) not in existing:
                    self.addItem(str(path))
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Subtitle Optimizer v1.0")
        self.resize(720, 480)

        title = QLabel("Subtitle Optimizer v1.0")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")

        subtitle = QLabel("拖入 CapCut 导出的 SRT 字幕，自动合并碎句，生成整句字幕。")
        subtitle.setStyleSheet("color: #555;")

        self.file_list = DropListWidget()
        self.file_list.setToolTip("把一个或多个 .srt 文件拖到这里")

        hint = QLabel("把 .srt 文件拖到上方列表，或者点击“选择 SRT 文件”。")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #666;")

        choose_btn = QPushButton("选择 SRT 文件")
        choose_btn.clicked.connect(self.choose_files)

        clear_btn = QPushButton("清空列表")
        clear_btn.clicked.connect(self.file_list.clear)

        max_label = QLabel("每条字幕最长字符数：")
        self.max_chars = QSpinBox()
        self.max_chars.setRange(60, 240)
        self.max_chars.setValue(120)
        self.max_chars.setSingleStep(10)

        self.output_preview = QLineEdit("输出文件会生成在原字幕旁边，文件名为 *_clean.srt")
        self.output_preview.setReadOnly(True)

        convert_btn = QPushButton("开始转换")
        convert_btn.setMinimumHeight(42)
        convert_btn.clicked.connect(self.convert_files)
        convert_btn.setStyleSheet("font-size: 16px; font-weight: bold;")

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(choose_btn)
        top_buttons.addWidget(clear_btn)

        settings = QHBoxLayout()
        settings.addWidget(max_label)
        settings.addWidget(self.max_chars)
        settings.addStretch()

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addWidget(self.file_list)
        layout.addWidget(hint)
        layout.addLayout(top_buttons)
        layout.addLayout(settings)
        layout.addWidget(self.output_preview)
        layout.addSpacing(10)
        layout.addWidget(convert_btn)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 SRT 字幕文件",
            str(Path.home() / "Downloads"),
            "SRT subtitles (*.srt)",
        )
        existing = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        for file in files:
            if file not in existing:
                self.file_list.addItem(file)

    def get_paths(self) -> List[Path]:
        return [Path(self.file_list.item(i).text()) for i in range(self.file_list.count())]

    def convert_files(self) -> None:
        paths = self.get_paths()
        if not paths:
            QMessageBox.warning(self, "没有文件", "请先拖入或选择至少一个 .srt 文件。")
            return

        successes: List[Path] = []
        failures: List[str] = []

        for path in paths:
            try:
                output = optimize_file(path, self.max_chars.value())
                successes.append(output)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{path.name}: {exc}")

        message = ""
        if successes:
            message += "已完成：\n" + "\n".join(str(p) for p in successes)
        if failures:
            message += "\n\n失败：\n" + "\n".join(failures)

        QMessageBox.information(self, "转换结果", message.strip())


def main(argv: Iterable[str] | None = None) -> int:
    app = QApplication(list(argv or sys.argv))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

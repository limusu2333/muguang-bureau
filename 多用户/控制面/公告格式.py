"""公告富文本的白名单清洗与纯文本提取。"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser


_颜色 = {
    "#b47a2f": "announcement-color-gold",
    "rgb(180, 122, 47)": "announcement-color-gold",
    "#256fa1": "announcement-color-blue",
    "rgb(37, 111, 161)": "announcement-color-blue",
    "#a64b45": "announcement-color-red",
    "rgb(166, 75, 69)": "announcement-color-red",
    "#38785d": "announcement-color-green",
    "rgb(56, 120, 93)": "announcement-color-green",
    "#745694": "announcement-color-purple",
    "rgb(116, 86, 148)": "announcement-color-purple",
}
_字体 = {
    "iowan old style": "announcement-font-serif",
    "songti sc": "announcement-font-serif",
    "inter": "announcement-font-sans",
    "pingfang sc": "announcement-font-sans",
    "sfmono-regular": "announcement-font-mono",
    "menlo": "announcement-font-mono",
    "monospace": "announcement-font-mono",
}
_字号 = {
    "2": "announcement-size-small",
    "12px": "announcement-size-small",
    "small": "announcement-size-small",
    "4": "announcement-size-large",
    "18px": "announcement-size-large",
    "large": "announcement-size-large",
}
_空白行 = re.compile(r"\n{3,}")


def _样式表(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in str(value or "").split(";"):
        key, separator, raw = part.partition(":")
        if separator:
            result[key.strip().lower()] = raw.strip().lower()
    return result


def _字体类(value: str) -> str:
    normalized = str(value or "").strip().strip("'\"").lower()
    for name, class_name in _字体.items():
        if name in normalized:
            return class_name
    return ""


class _公告清洗器(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html: list[str] = []
        self.text: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0

    def _换行(self) -> None:
        if self.text and not self.text[-1].endswith("\n"):
            self.text.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth += 1
            return
        if tag in {"script", "style", "iframe", "object", "svg", "math"}:
            self.skip_depth = 1
            return
        if tag in {"area", "base", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        styles = _样式表(values.get("style", ""))
        output = ""
        if tag == "br":
            self.html.append("<br>")
            self._换行()
            return
        if tag in {"b", "strong"}:
            output = "strong"
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._换行()
            output = "h3"
        elif tag in {"p", "div"}:
            self._换行()
            output = "p"
        elif tag in {"ul", "ol", "li"}:
            self._换行()
            output = tag
            if tag == "li":
                self.text.append("• ")
        elif tag in {"span", "font"}:
            classes: list[str] = []
            color = values.get("color", "").strip().lower() or styles.get("color", "")
            font = values.get("face", "") or styles.get("font-family", "")
            size = values.get("size", "") or styles.get("font-size", "")
            for class_name in (_颜色.get(color, ""), _字体类(font), _字号.get(size, "")):
                if class_name and class_name not in classes:
                    classes.append(class_name)
            bold = styles.get("font-weight") in {"600", "700", "800", "900", "bold", "bolder"}
            opening = "<strong>" if bold else ""
            closing = "</strong>" if bold else ""
            if classes:
                opening += f'<span class="{" ".join(classes)}">'
                closing = "</span>" + closing
            self.html.append(opening)
            self.stack.append(closing)
            return
        if output:
            self.html.append(f"<{output}>")
        self.stack.append(f"</{output}>" if output else "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br" and not self.skip_depth:
            self.html.append("<br>")
            self._换行()

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag.lower() == "br" or not self.stack:
            return
        closing = self.stack.pop()
        if closing:
            self.html.append(closing)
            if any(f"</{tag}>" in closing for tag in {"p", "h3", "li", "ul", "ol"}):
                self._换行()

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data:
            return
        self.html.append(escape(data, quote=False))
        self.text.append(data)


def 清洗公告正文(value: str) -> tuple[str, str]:
    """返回经过白名单清洗的富文本和用于检索/审计的纯文本。"""
    parser = _公告清洗器()
    parser.feed(str(value or ""))
    parser.close()
    rich = "".join(parser.html).strip()
    plain = _空白行.sub("\n\n", "".join(parser.text)).strip()
    return rich, plain

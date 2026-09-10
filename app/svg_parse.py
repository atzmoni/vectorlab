from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .config import MAX_SVG_CHARS


def _root(svg: str) -> ET.Element:
    if not isinstance(svg, str) or not svg.strip() or len(svg) > MAX_SVG_CHARS:
        raise ValueError("SVG document is empty or exceeds the size limit")
    try:
        return ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc


def parse_viewbox(svg: str) -> tuple[float, float]:
    root = _root(svg)
    viewbox = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(viewbox) == 4:
        try:
            w, h = float(viewbox[2]), float(viewbox[3])
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass

    def numeric(v: str) -> float:
        m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v)
        return float(m.group(0)) if m else 0.0

    return max(1.0, numeric(root.attrib.get("width", ""))), max(1.0, numeric(root.attrib.get("height", "")))


def parse_style_classes(svg: str) -> dict[str, dict[str, str]]:
    classes: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", svg, flags=re.DOTALL | re.IGNORECASE):
        text = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
        for cm in re.finditer(r"\.([A-Za-z0-9_-]+)\s*\{([^}]+)\}", text):
            props: dict[str, str] = {}
            for part in cm.group(2).split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    props[k.strip().lower()] = v.strip()
            classes[cm.group(1)] = props
    return classes


def collect_g_fills(svg: str) -> dict[ET.Element, str]:
    try:
        root = _root(svg)
    except Exception:
        return {}
    g_fill: dict[ET.Element, str] = {}

    def walk(elem: ET.Element, inherited: str | None) -> None:
        cur = inherited
        if "fill" in elem.attrib:
            cur = elem.attrib["fill"].strip()
        style = elem.attrib.get("style", "")
        if style:
            for part in style.split(";"):
                if "fill" in part.lower():
                    k, v = part.split(":", 1) if ":" in part else ("", "")
                    if k.strip().lower() == "fill":
                        cur = v.strip()
        g_fill[elem] = cur if cur is not None else ""
        for child in elem:
            walk(child, cur)

    walk(root, None)
    return g_fill


def effective_style(elem: ET.Element, style_classes: dict[str, dict[str, str]], g_fill: dict[ET.Element, str]) -> dict[str, str]:
    style: dict[str, str] = {}
    for cls in elem.attrib.get("class", "").split():
        if cls in style_classes:
            style.update(style_classes[cls])
    inline = elem.attrib.get("style", "")
    if inline:
        for part in inline.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                style[k.strip().lower()] = v.strip()
    for key in ("fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity"):
        if key in elem.attrib:
            style[key] = elem.attrib[key].strip()
    if "fill" not in style and not elem.attrib.get("class"):
        inherited = g_fill.get(elem, "")
        if inherited and inherited.lower() != "none":
            style["fill"] = inherited
    return style


def extract_path_data(svg: str) -> list[str]:
    paths: list[str] = []
    for element in _root(svg).iter():
        tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
        if tag == "path" and element.attrib.get("d", "").strip():
            paths.append(element.attrib["d"].strip())
    return paths


def extract_path_infos(svg: str) -> list[dict]:
    infos: list[dict] = []
    style_classes = parse_style_classes(svg)
    g_fill = collect_g_fills(svg)
    for element in _root(svg).iter():
        tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
        if tag != "path":
            continue
        d = element.attrib.get("d", "").strip()
        if not d:
            continue
        eff = effective_style(element, style_classes, g_fill)
        fill = eff.get("fill", "")
        stroke = eff.get("stroke", "")
        fill_is_none = fill.lower() == "none" if fill else False
        stroke_is_none = stroke.lower() == "none" if stroke else True
        if not fill and not stroke and not element.attrib.get("class"):
            parent_fill = g_fill.get(element, "")
            if parent_fill and parent_fill.lower() != "none":
                fill, fill_is_none = parent_fill, False
            else:
                fill, fill_is_none = "#000000", False
        if "fill-opacity" in eff:
            try:
                if float(eff["fill-opacity"]) == 0:
                    fill_is_none = True
            except ValueError:
                pass
        infos.append({
            "d": d,
            "fill": fill if not fill_is_none else "none",
            "stroke": stroke if not stroke_is_none else "none",
            "fill_is_none": fill_is_none,
            "stroke_is_none": stroke_is_none,
            "transform": element.attrib.get("transform", "").strip(),
        })
    return infos


def parse_transform(transform: str) -> tuple[float, float, float, float, float, float] | None:
    if not transform:
        return None
    m = re.search(r"matrix\s*\(\s*([^)]+)\)", transform, flags=re.IGNORECASE)
    if m:
        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", m.group(1))]
        if len(nums) >= 6:
            return (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
    return None

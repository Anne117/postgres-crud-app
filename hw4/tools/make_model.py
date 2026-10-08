"""Генерация файлов модели: drawio-диаграмма потоков данных, PNG/SVG,
модель для OWASP Threat Dragon (формат v2) и данные для отчёта.

Запуск:  python tools/make_model.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import catalog as C  # noqa: E402

MODEL_DIR = ROOT / "model"
MODEL_DIR.mkdir(exist_ok=True)
DRAWIO = r"C:\Program Files\draw.io\draw.io.exe"
NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# --------------------------------------------------------------------------- #
# Соответствие элементов диаграммы и локализованных на них угроз             #
# --------------------------------------------------------------------------- #
def threats_on(element_id: str) -> list[str]:
    return [t["id"] for t in C.THREATS if t["target"] == element_id]


def elem_label(el: dict) -> str:
    ids = threats_on(el["id"])
    label = el["label"]
    if el["kind"] in ("actor", "process", "store") and ids:
        label += "\n[" + ", ".join(ids) + "]"
    return label


# --------------------------------------------------------------------------- #
# 1. draw.io / diagrams.net                                                  #
# --------------------------------------------------------------------------- #
STYLE_ACTOR = ("rounded=0;whiteSpace=wrap;html=1;fillColor=#DAE8FC;"
               "strokeColor=#6C8EBF;strokeWidth=2;fontSize=12;fontStyle=1;")
STYLE_PROCESS = ("shape=ellipse;perimeter=ellipsePerimeter;whiteSpace=wrap;html=1;"
                 "fillColor=#D5E8D4;strokeColor=#82B366;strokeWidth=2;fontSize=11;")
STYLE_STORE = ("shape=partialRectangle;top=1;bottom=1;left=0;right=0;"
               "whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#666666;"
               "strokeWidth=2;fontSize=10;")
STYLE_TEXT = ("text;html=1;strokeColor=none;fillColor=none;align=left;"
              "verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=3;")
STYLE_BOUNDARY = ("rounded=0;dashed=1;dashPattern=12 12;fillColor=none;"
                  "strokeColor=#B85450;strokeWidth=2;verticalAlign=top;align=left;"
                  "fontSize=13;fontStyle=1;spacingLeft=10;spacingTop=8;")
STYLE_FLOW = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;"
              "endFill=1;strokeWidth=2;strokeColor=#0B5394;fontSize=10;"
              "labelBackgroundColor=#FFFFFF;")
STYLE_FLOW_BORDER = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;"
                     "endFill=1;strokeWidth=2;strokeColor=#CC0000;fontSize=10;"
                     "dashed=1;labelBackgroundColor=#FFFFFF;")


def _geom(x, y, w, h) -> str:
    return (f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" '
            f'as="geometry"/>')


def xattr(text: str) -> str:
    """Экранирование значения XML-атрибута (в т.ч. кавычки и переводы строк)."""
    return escape(text, {'"': "&quot;"}).replace("\n", "&#10;")


def _label_size(label: str) -> "tuple[float, float]":
    lines = label.split("\n")
    w = max(len(l) for l in lines) * 5.2 + 14
    h = 15 * len(lines) + 6
    return w, h


def label_offsets() -> "list[tuple[float, float]]":
    """Жадное размещение подписей потоков без наложения друг на друга
    и без перекрытия элементов диаграммы (offset в mxGeometry)."""
    centers = {e["id"]: (e["x"] + e["w"] / 2.0, e["y"] + e["h"] / 2.0)
               for e in C.ELEMENTS}
    boxes = [(e["x"] - 6, e["y"] - 6, e["x"] + e["w"] + 6, e["y"] + e["h"] + 6)
             for e in C.ELEMENTS if e["kind"] != "text"]
    steps = [(0, 0)]
    for r in (70, 140, 210, 280, 350):
        for dy in (0, 36, 72, 108, 144):
            for sx in (1, -1):
                steps.append((sx * r, dy))
                if dy:
                    steps.append((sx * r, -dy))
    placed = []
    offsets = []
    for i, (src, dst, label, _enc) in enumerate(C.FLOWS, start=1):
        if src not in centers or dst not in centers:
            offsets.append((0, 0))
            continue
        body = label.split(". ", 1)[-1]
        w, h = _label_size(body)
        ax, ay = centers[src]
        bx, by = centers[dst]
        base = ((ax + bx) / 2.0, (ay + by) / 2.0)
        best = (0.0, 0.0)
        for dx, dy in steps:
            cx, cy = base[0] + dx, base[1] + dy
            x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            if any(bx1 < x2 and x1 < bx2 and by1 < y2 and y1 < by2
                   for bx1, by1, bx2, by2 in placed):
                continue
            if any(bx1 < x2 and x1 < bx2 and by1 < y2 and y1 < by2
                   for bx1, by1, bx2, by2 in boxes):
                continue
            best = (float(dx), float(dy))
            break
        placed.append((base[0] + best[0] - w / 2, base[1] + best[1] - h / 2,
                       base[0] + best[0] + w / 2, base[1] + best[1] + h / 2))
        offsets.append(best)
    return offsets


def build_drawio() -> str:
    cell = []
    add = cell.append
    add('<mxCell id="0"/>')
    add('<mxCell id="1" parent="0"/>')

    # заголовок
    add('<mxCell id="title" value="%s" style="%s" vertex="1" parent="1">%s</mxCell>'
        % (xattr(C.DFD_TITLE), STYLE_TEXT + "fontSize=16;fontStyle=1;",
           _geom(60, 40, 1480, 50)))

    # границы доверия
    for b in C.BOUNDARIES:
        add('<mxCell id="%s" value="%s" style="%s" vertex="1" parent="1">%s</mxCell>'
            % (b["id"], xattr(b["name"]), STYLE_BOUNDARY,
               _geom(b["x"], b["y"], b["w"], b["h"])))

    # элементы
    styles = {"actor": STYLE_ACTOR, "process": STYLE_PROCESS,
              "store": STYLE_STORE, "text": STYLE_TEXT}
    for el in C.ELEMENTS:
        add('<mxCell id="%s" value="%s" style="%s" vertex="1" parent="1">%s</mxCell>'
            % (el["id"], xattr(elem_label(el)), styles[el["kind"]],
               _geom(el["x"], el["y"], el["w"], el["h"])))

    # потоки данных
    offs = label_offsets()
    for i, (src, dst, label, encrypted) in enumerate(C.FLOWS, start=1):
        style = _flow_style(src, dst)
        ox, oy = offs[i - 1]
        geom = ('<mxGeometry relative="1" as="geometry">'
                '<mxPoint x="%d" y="%d" as="offset"/></mxGeometry>'
                % (round(ox), round(oy))) if (ox or oy) else \
               '<mxGeometry relative="1" as="geometry"/>'
        add('<mxCell id="f%d" value="%s" style="%s" edge="1" parent="1" '
            'source="%s" target="%s">%s</mxCell>'
            % (i, xattr(label), style, src, dst, geom))

    body = "\n        ".join(cell)
    return (
        '<mxfile host="app.diagrams.net" agent="hw4-model-generator" '
        'version="24.7.17" type="device">\n'
        '  <diagram id="dfd-crud-app" name="DFD — CRUD app (STRIDE)">\n'
        '    <mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        'pageWidth="1860" pageHeight="1120" math="0" shadow="0">\n'
        '      <root>\n        %s\n      </root>\n'
        '    </mxGraphModel>\n  </diagram>\n</mxfile>\n' % body
    )


def _flow_style(src: str, dst: str) -> str:
    """Поток от нарушителя к внутреннему элементу рисуется красным пунктиром."""
    if src != "attacker":
        return STYLE_FLOW
    for el in C.ELEMENTS:
        if el["id"] == dst:
            return STYLE_FLOW_BORDER if el["kind"] != "actor" else STYLE_FLOW
    return STYLE_FLOW


def _crosses(src: str, dst: str) -> bool:
    """Поток пересекает границу доверия контейнера (нарушитель -> контейнер)."""
    if src != "attacker":
        return False
    return dst in ("p_app", "p_pg", "ds_db", "ds_log")


# --------------------------------------------------------------------------- #
# 2. Модель OWASP Threat Dragon (формат v2)                                  #
# --------------------------------------------------------------------------- #
def _uuid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def _severity(score: float) -> str:
    if score >= 18:
        return "High"
    if score >= 14:
        return "Medium"
    return "Low"


def _td_threat(t: dict) -> dict:
    return {
        "id": _uuid("threat:" + t["id"]),
        "status": "Open",
        "severity": _severity(t["score"]),
        "title": f"{t['id']}: {t['title']}",
        "type": _stride_type(t["stride"]),
        "description": f"{t['desc']} Оценка DREAD: {t['score']:.0f}/25.",
        "mitigation": t["mitigation"],
        "modelType": "STRIDE",
    }


def _stride_type(letter: str) -> str:
    return {
        "S": "Spoofing", "T": "Tampering", "R": "Repudiation",
        "I": "Information disclosure", "D": "Denial of service",
        "E": "Elevation of privilege",
    }[letter]


def _ports() -> dict:
    def group(pos):
        return {"position": pos, "attrs": {"circle": {
            "r": 4, "magnet": True, "stroke": "#5F95FF", "strokeWidth": 1,
            "fill": "#fff", "style": {"visibility": "hidden"}}}}
    return {"groups": {p: group(p) for p in ("top", "right", "bottom", "left")}}


def build_threat_dragon() -> dict:
    cells, threats = [], []
    by_target: dict[str, list[dict]] = {}
    for t in C.ranked():
        by_target.setdefault(t["target"], []).append(t)

    # границы доверия
    for b in C.BOUNDARIES:
        cells.append({
            "id": _uuid("boundary:" + b["id"]),
            "shape": "trust-boundary-curve",
            "attrs": {"line": {"targetMarker": "", "sourceMarker": ""}},
            "width": 200, "height": 100, "zIndex": 10,
            "labels": [{
                "markup": [{"tagName": "ellipse", "selector": "labelBody"},
                           {"tagName": "text", "selector": "labelText"}],
                "attrs": {
                    "labelText": {"text": b["name"], "textAnchor": "middle",
                                  "textVerticalAnchor": "middle"},
                    "labelBody": {"ref": "labelText", "refRx": "50%",
                                  "refRy": "60%", "fill": "#fff",
                                  "strokeWidth": 0},
                    "text": {"text": b["name"]},
                },
                "position": {"distance": 0.5, "args": {
                    "keepGradient": True, "ensureLegibility": True}},
            }],
            "connector": "smooth",
            "data": {"type": "tm.Boundary", "name": b["name"],
                     "description": "", "isTrustBoundary": True,
                     "hasOpenThreats": False},
            "source": {"x": b["x"], "y": b["y"]},
            "target": {"x": b["x"] + b["w"], "y": b["y"] + b["h"]},
            "vertices": [
                {"x": b["x"] + b["w"], "y": b["y"]},
                {"x": b["x"], "y": b["y"] + b["h"]},
            ],
        })

    # потоки данных
    for i, (src, dst, label, _enc) in enumerate(C.FLOWS, start=1):
        cells.append({
            "id": _uuid(f"flow:{i}"),
            "shape": "flow",
            "attrs": {"line": {"stroke": "#0B5394",
                               "targetMarker": {"name": "block"},
                               "strokeDasharray": None}},
            "width": 200, "height": 100, "zIndex": 10,
            "labels": [{
                "markup": [{"tagName": "ellipse", "selector": "labelBody"},
                           {"tagName": "text", "selector": "labelText"}],
                "attrs": {
                    "labelText": {"text": label.replace("\\n", " "),
                                  "textAnchor": "middle",
                                  "textVerticalAnchor": "middle"},
                    "labelBody": {"ref": "labelText", "refRx": "50%",
                                  "refRy": "60%", "fill": "#fff",
                                  "strokeWidth": 0},
                },
                "position": 0.5,
            }],
            "connector": "smooth",
            "source": {"cell": _uuid("node:" + src)},
            "target": {"cell": _uuid("node:" + dst)},
            "data": {"type": "tm.Flow", "name": label.replace("\\n", " "),
                     "description": "", "outOfScope": False,
                     "reasonOutOfScope": "", "hasOpenThreats": False,
                     "isBidirectional": False, "isEncrypted": False,
                     "isPublicNetwork": False, "protocol": ""},
        })

    # элементы
    kind_map = {"actor": ("Actor", "actor"), "process": ("Process", "process"),
                "store": ("Store", "store"), "text": ("Text", "td-text-block")}
    for el in C.ELEMENTS:
        ttype, shape = kind_map[el["kind"]]
        el_threats = by_target.get(el["id"], [])
        threats.extend(el_threats)
        data = {
            "type": f"tm.{ttype}",
            "name": el["label"].replace("\\n", " "),
            "description": el["desc"],
            "outOfScope": False,
            "reasonOutOfScope": "",
            "hasOpenThreats": bool(el_threats),
            "threats": [_td_threat(t) for t in el_threats],
        }
        if ttype == "Actor":
            data["isTrustBoundary"] = False
            data["providesAuthentication"] = False
        if ttype == "Store":
            data["isALog"] = el["id"] == "ds_log"
            data["storesCredentials"] = el["id"] in ("ds_creds", "ds_conf")
            data["isEncrypted"] = False
            data["isSigned"] = False
        cell = {
            "position": {"x": el["x"], "y": el["y"]},
            "size": {"width": el["w"], "height": el["h"]},
            "attrs": {"text": {"text": elem_label(el).replace("\\n", "\n")}},
            "visible": True, "shape": shape, "zIndex": 1,
            "id": _uuid("node:" + el["id"]), "data": data,
        }
        if el["kind"] in ("actor", "process", "store"):
            cell["ports"] = _ports()
            if el["kind"] == "actor":
                cell["attrs"]["body"] = {"stroke": "#6C8EBF", "strokeWidth": 2,
                                         "strokeDasharray": None}
            elif el["kind"] == "process":
                cell["attrs"]["body"] = {"stroke": "#82B366", "strokeWidth": 2,
                                         "strokeDasharray": None}
            else:
                cell["attrs"]["topLine"] = {"stroke": "#666666", "strokeWidth": 2,
                                            "strokeDasharray": None}
                cell["attrs"]["bottomLine"] = {"stroke": "#666666",
                                               "strokeWidth": 2,
                                               "strokeDasharray": None}
        cells.append(cell)

    return {
        "summary": {
            "title": "CRUD-приложение «склад интернет-магазина» — модель угроз",
            "owner": "Anya",
            "description": "Задание №4: DFD и локализованные угрозы STRIDE "
                           "для приложения из задания №3 (postgres-crud-app). "
                           "Оценка рисков по методике DREAD.",
        },
        "detail": {
            "contributors": [{"name": "Anya"}],
            "reviewer": "Преподаватель",
            "diagrams": [{
                "version": "2.3.0",
                "title": "DFD — CRUD app (STRIDE)",
                "description": "Контекстная и нулевая диаграмма потоков данных "
                               "уровня контейнеров Docker.",
                "thumbnail": "",
                "diagramType": "STRIDE",
                "id": 0,
                "cells": cells,
            }],
            "diagramTop": 0,
            "threatTop": len(threats),
        },
        "version": "2.3.0",
    }


# --------------------------------------------------------------------------- #
# 3. Экспорт PNG/SVG через draw.io CLI                                        #
# --------------------------------------------------------------------------- #
def export(drawio_file: Path, fmt: str) -> bool:
    out = drawio_file.with_suffix("." + fmt)
    cmd = [DRAWIO, "-x", "-f", fmt, "-b", "10", "--width", "2400",
           "-o", str(out), str(drawio_file)]
    if fmt == "png":
        cmd = [DRAWIO, "-x", "-f", "png", "--scale", "2", "-b", "10",
               "-o", str(out), str(drawio_file)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = out.exists() and out.stat().st_size > 1000
    print(f"  {fmt}: {'OK' if ok else 'FAIL'} "
          f"({out.stat().st_size if out.exists() else 0} байт) "
          f"{r.stderr.strip()[:200]}")
    return ok


# --------------------------------------------------------------------------- #
# 4. Данные для отчёта                                                        #
# --------------------------------------------------------------------------- #
def build_report_data() -> dict:
    ranked = C.ranked()
    top5 = ranked[:5]
    return {
        "title": C.DFD_TITLE,
        "boundaries": C.BOUNDARIES,
        "elements": [dict(el, threats=threats_on(el["id"])) for el in C.ELEMENTS],
        "flows": [{"n": i, "src": s, "dst": d, "label": l,
                   "crosses": _crosses(s, d)}
                  for i, (s, d, l, _e) in enumerate(C.FLOWS, start=1)],
        "threats": ranked,
        "top5": [dict(t, place=i) for i, t in enumerate(top5, start=1)],
        "dread_labels": C.DREAD_LABELS,
        "stride_ru": C.STRIDE_RU,
    }


def main() -> None:
    drawio_xml = build_drawio()
    drawio_file = MODEL_DIR / "dfd-crud-app.drawio"
    drawio_file.write_text(drawio_xml, encoding="utf-8")
    print(f"drawio: {drawio_file} ({len(drawio_xml)} байт)")

    td = build_threat_dragon()
    td_file = MODEL_DIR / "threat-dragon-crud-app.json"
    td_file.write_text(json.dumps(td, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    n_cells = len(td["detail"]["diagrams"][0]["cells"])
    print(f"threat dragon: {td_file} cells={n_cells} threats={td['detail']['threatTop']}")

    for fmt in ("svg", "png"):
        export(drawio_file, fmt)

    data = build_report_data()
    (ROOT / "model" / "report-data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report data: {len(data['threats'])} угроз, "
          f"top5={[t['id'] for t in data['top5']]}")


if __name__ == "__main__":
    main()

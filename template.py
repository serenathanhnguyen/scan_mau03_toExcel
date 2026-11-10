"""Đọc / ghi file mẫu Medinet ở mức XML.

Vì sao không dùng openpyxl để lưu: file mẫu có bảng (table), dropdown dạng x14, công thức mảng
và XLOOKUP; openpyxl lưu lại sẽ làm mất/hỏng các phần này. Ở đây chỉ sửa 3 phần trong file .xlsx:
  - sheet dữ liệu chính (ghi giá trị vào ô)
  - sharedStrings.xml (thêm chuỗi mới)
  - workbook.xml (bật tính lại công thức khi mở)
Mọi thứ khác được copy nguyên.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

from lxml import etree

from .transform import Lookups

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_RNS = "http://schemas.openxmlformats.org/package/2006/relationships"
Q = lambda t: f"{{{NS}}}{t}"  # noqa: E731


def col_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def split_ref(ref: str) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    return m.group(1), int(m.group(2))


@dataclass
class Column:
    letter: str
    keyword: str
    label: str        # nhãn dễ đọc (nhóm › cột)
    required: bool
    hint: str


class Template:
    def __init__(self, data: bytes, settings: dict):
        self.data = data
        self.ex = settings["excel"]
        self.zf = zipfile.ZipFile(io.BytesIO(data))
        self.sheet_path = self._sheet_path(self.ex.get("sheet_name", "ThongTinHanhChinh"))
        self.sst = self._shared_strings()
        self._read_header()

    # ------------------------------------------------------------ structure
    def _sheet_path(self, name: str) -> str:
        wb = etree.fromstring(self.zf.read("xl/workbook.xml"))
        rels = etree.fromstring(self.zf.read("xl/_rels/workbook.xml.rels"))
        rid = next((s.get(f"{{{RNS}}}id") for s in wb.iter(Q("sheet")) if s.get("name") == name), None)
        if rid is None:
            raise ValueError(f"Không thấy sheet '{name}' trong file mẫu")
        target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        return "xl/" + target.lstrip("/").removeprefix("xl/")

    def _shared_strings(self) -> list[str]:
        root = etree.fromstring(self.zf.read("xl/sharedStrings.xml"))
        out = []
        for si in root.findall(Q("si")):
            out.append("".join(t.text or "" for t in si.iter(Q("t")) if t.getparent().tag != Q("rPh")))
        return out

    def _cell_text(self, c) -> str:
        t = c.get("t")
        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(Q("t")))
        v = c.find(Q("v"))
        if v is None or v.text is None:
            return ""
        return self.sst[int(v.text)] if t == "s" else v.text

    def _read_header(self):
        root = etree.fromstring(self.zf.read(self.sheet_path))
        rows = {int(r.get("r")): r for r in root.find(Q("sheetData"))}
        rk, rl, rs, rh, r1 = (self.ex[k] for k in ("keyword_row", "label_row", "sub_label_row",
                                                   "patient_first_row", "hint_row"))
        cells = lambda rn: {split_ref(c.get("r"))[0]: self._cell_text(c).strip() for c in rows.get(rn, [])}  # noqa: E731
        kw, lab, sub, hint, sample = cells(rk), cells(rl), cells(rs), cells(r1), cells(rh)
        self.columns: list[Column] = []
        group = ""
        for letter in sorted(set(kw) | set(lab), key=col_idx):
            if lab.get(letter, "") not in ("", "-"):
                group = lab[letter]
            k = kw.get(letter, "")
            if not k or k == "-":
                continue
            s = sub.get(letter, "")
            label = f"{group} › {s}" if s and s != group else (s or group)
            label = re.sub(r"\s+", " ", label).strip()
            self.columns.append(Column(letter, k, label.replace(" *", "").replace("*", "").strip(),
                                       "*" in (lab.get(letter, "") + s), hint.get(letter, "")))
        self.keywords = [c.keyword for c in self.columns]
        self.by_keyword = {c.keyword: c for c in self.columns}
        self.sample = {c.keyword: sample.get(c.letter, "") for c in self.columns}
        self.required = {c.keyword for c in self.columns if c.required}

    # ------------------------------------------------------------ lookups
    def lookups(self) -> Lookups:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(self.data), read_only=True, data_only=True)
        lk = Lookups()
        for name, cid, *_ in wb["Tinh"].iter_rows(min_row=2, values_only=True):
            if name:
                lk.provinces[str(name).strip()] = cid
        for name, wid, _code, cid in wb["PhuongXa"].iter_rows(min_row=2, max_col=4, values_only=True):
            if name:
                lk.wards.append((str(name).strip(), wid, cid))
        for name, code in wb["NgheNghiep"].iter_rows(min_row=2, max_col=2, values_only=True):
            if name:
                lk.occupations[str(name).strip()] = code
        wb.close()
        return lk

    # ------------------------------------------------------------ writing
    def build(self, records: list[dict], formula_cache: list[dict[str, str]] | None = None) -> bytes:
        """records: [{keyword: str}]; formula_cache: [{col_letter: giá trị đã tính}] cho cột công thức."""
        first = self.ex["patient_first_row"]
        stt_col = self.ex.get("stt_column")
        root = etree.fromstring(self.zf.read(self.sheet_path))
        rows = {int(r.get("r")): r for r in root.find(Q("sheetData"))}

        sst_root = etree.fromstring(self.zf.read("xl/sharedStrings.xml"))
        new_idx: dict[str, int] = {}
        n_si = len(sst_root.findall(Q("si")))
        n_refs = 0

        def sst_index(text: str) -> int:
            nonlocal n_refs
            n_refs += 1
            if text not in new_idx:
                si = etree.SubElement(sst_root, Q("si"))
                t = etree.SubElement(si, Q("t"))
                t.text = text
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                new_idx[text] = n_si + len(new_idx)
            return new_idx[text]

        def get_cell(row, letter):
            ref = f"{letter}{row.get('r')}"
            for c in row:
                if c.get("r") == ref:
                    return c
            c = etree.Element(Q("c"), r=ref)
            pos = 0
            for i, ex in enumerate(row):
                if col_idx(split_ref(ex.get("r"))[0]) < col_idx(letter):
                    pos = i + 1
            row.insert(pos, c)
            return c

        def clear_value(c):
            for ch in list(c):
                if ch.tag in (Q("v"), Q("is")):
                    c.remove(ch)
            c.attrib.pop("t", None)

        def put(c, text: str):
            clear_value(c)
            if text == "":
                return
            c.set("t", "s")
            etree.SubElement(c, Q("v")).text = str(sst_index(text))

        for i, rec in enumerate(records):
            rn = first + i
            if rn not in rows:
                raise ValueError(f"File mẫu không có dòng {rn} (tối đa {max(rows)} dòng)")
            row = rows[rn]
            if stt_col:
                c = get_cell(row, stt_col)
                clear_value(c)
                etree.SubElement(c, Q("v")).text = str(i + 1)
            for col in self.columns:
                if col.keyword not in rec:
                    continue
                c = get_cell(row, col.letter)
                if c.find(Q("f")) is not None:      # cột công thức: không ghi đè
                    continue
                put(c, rec[col.keyword])
            for letter, val in (formula_cache[i] if formula_cache else {}).items():
                c = get_cell(row, letter)
                if c.find(Q("f")) is None:
                    continue
                for ch in c.findall(Q("v")):
                    c.remove(ch)
                v = etree.SubElement(c, Q("v"))
                v.text = str(val)
                if str(val) == "" or not re.fullmatch(r"-?\d+(\.\d+)?", str(val)):
                    c.set("t", "str")
                else:
                    c.attrib.pop("t", None)

        sst_root.set("uniqueCount", str(int(sst_root.get("uniqueCount", n_si)) + len(new_idx)))
        sst_root.set("count", str(int(sst_root.get("count", n_si)) + n_refs))

        wb_xml = self.zf.read("xl/workbook.xml").decode("utf8")
        if "<calcPr" in wb_xml:
            wb_xml = re.sub(r"<calcPr([^>]*?)/>", lambda m: "<calcPr" + re.sub(r'\s*fullCalcOnLoad="[^"]*"', "", m.group(1)) + ' fullCalcOnLoad="1"/>', wb_xml, count=1)
        else:
            wb_xml = wb_xml.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')

        replace = {
            self.sheet_path: etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True),
            "xl/sharedStrings.xml": etree.tostring(sst_root, xml_declaration=True, encoding="UTF-8", standalone=True),
            "xl/workbook.xml": wb_xml.encode("utf8"),
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
            for info in self.zf.infolist():
                zo.writestr(info.filename, replace.get(info.filename, self.zf.read(info.filename)))
        return out.getvalue()


def compute_formula_cache(settings: dict, rec: dict, lk: Lookups) -> dict[str, str]:
    """Tính sẵn giá trị đệm cho các cột công thức tra cứu (VLOOKUP/XLOOKUP) của mẫu, để importer
    nào đọc giá trị đệm (không tính lại công thức) vẫn thấy mã đúng. Excel vẫn tính lại khi mở."""
    out: dict[str, str] = {}
    cf = lambda d: {k.casefold(): v for k, v in d.items()}  # noqa: E731
    prov, occ = cf(lk.provinces), cf(lk.occupations)
    for spec in settings.get("formula_cache", []):
        col, kind = spec["col"], spec["kind"]
        if kind == "occupation_id":
            out[col] = str(occ.get(rec.get(spec["from"], "").casefold(), ""))
        elif kind == "province_id":
            out[col] = str(prov.get(rec.get(spec["from"], "").casefold(), ""))
        elif kind == "ward_id":
            city = prov.get(rec.get(spec["city_from"], "").casefold())
            name = rec.get(spec["from"], "").casefold()
            out[col] = next((str(w[1]) for w in lk.wards if w[2] == city and w[0].casefold() == name), "")
    return out

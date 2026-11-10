"""Streamlit: PDF scan Giấy KSK Mẫu 03 -> Excel import Medinet.   Chạy:  streamlit run app.py"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st

from m03 import pdf as pdfmod
from m03.config import CONFIG_DIR, load_config
from m03.extract import detect_patient_starts
from m03.pipeline import export_xlsx, process_all, schema_report
from m03.template import Template

ROOT = Path(__file__).parent
st.set_page_config(page_title="Mẫu 03 → Medinet", page_icon="🩺", layout="wide")


# ------------------------------------------------------------------ cache
def _cfg_stamp() -> float:
    return max(p.stat().st_mtime for p in CONFIG_DIR.glob("*.yaml"))


@st.cache_resource
def get_cfg(stamp: float):          # đổi yaml là tự nạp lại, không cần restart
    return load_config()


@st.cache_resource(show_spinner="Đang đọc file mẫu Excel...")
def get_template(data: bytes, stamp: float):
    cfg = get_cfg(stamp)
    tpl = Template(data, cfg.settings)
    return tpl, tpl.lookups()


@st.cache_data(show_spinner=False)
def blank_pages(key: str, ink: float, _pdf: bytes):
    return pdfmod.blank_flags(_pdf, ink)


@st.cache_data(show_spinner=False)
def page_img(key: str, page: int, _pdf: bytes):
    return pdfmod.render(_pdf, [page], dpi=110)[0]


cfg = get_cfg(_cfg_stamp())

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Cài đặt")
    api_key = st.text_input("Anthropic API key", type="password",
                            value=os.environ.get("ANTHROPIC_API_KEY", ""))
    model = st.text_input("Model", value=cfg.settings["claude"]["model"])
    cfg.settings["claude"]["model"] = model
    st.caption("Ảnh phiếu khám được gửi tới Anthropic API để đọc. Chỉ dùng khi đơn vị cho phép xử lý dữ liệu sức khỏe qua dịch vụ ngoài.")

    st.subheader("File mẫu Excel (Medinet)")
    up_tpl = st.file_uploader("Tải mẫu mới nhất từ Medinet", type=["xlsx"], key="tpl")
    local = sorted((ROOT / "templates").glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    if up_tpl:
        tpl_bytes, tpl_name = up_tpl.getvalue(), up_tpl.name
    elif local:
        tpl_bytes, tpl_name = local[-1].read_bytes(), local[-1].name
    else:
        st.error("Chưa có file mẫu: tải lên hoặc bỏ vào thư mục templates/")
        st.stop()
    st.caption(f"Đang dùng: {tpl_name}")

    st.subheader("Chia hồ sơ trong PDF")
    mode = st.radio("Cách chia", ["Cố định số trang / bệnh nhân", "AI tự nhận diện trang đầu"], index=0)
    per = st.number_input("Số trang / bệnh nhân", 1, 40, int(cfg.settings["pdf"]["pages_per_patient"]),
                          disabled=mode.startswith("AI"))

tpl, lk = get_template(tpl_bytes, _cfg_stamp())

st.title("Giấy khám sức khỏe Mẫu 03 → Excel import Medinet")

# ------------------------------------------------------------------ schema check
rep = schema_report(cfg, tpl)
if rep["new_columns"]:
    st.warning(f"File mẫu có {len(rep['new_columns'])} cột mới chưa khai báo trong config/fields.yaml. "
               "Tool tạm điền theo dữ liệu mẫu ở dòng 5. Muốn đọc từ PDF: thêm 1 dòng cho mỗi keyword dưới đây.")
    st.dataframe(pd.DataFrame([{"Cột": c.letter, "keyword": c.keyword, "Tên cột": c.label,
                                "Gợi ý trong mẫu": c.hint, "Mẫu dòng 5": tpl.sample[c.keyword]}
                               for c in rep["new_columns"]]), hide_index=True, use_container_width=True)
if rep["removed_keywords"]:
    st.info("Các keyword sau có trong fields.yaml nhưng không còn trong mẫu (tool tự bỏ qua; có thể xoá khỏi yaml): "
            + ", ".join(rep["removed_keywords"]))
if not rep["new_columns"] and not rep["removed_keywords"]:
    st.success(f"File mẫu khớp config: {rep['n_columns']} cột.")

# ------------------------------------------------------------------ upload + split
pdf_file = st.file_uploader("PDF scan hồ sơ khám", type=["pdf"])
if pdf_file:
    pdf = pdf_file.getvalue()
    key = hashlib.md5(pdf).hexdigest()
    n = pdfmod.page_count(pdf)
    pcfg = cfg.settings["pdf"]
    blank = blank_pages(key, pcfg["blank_ink_ratio"], pdf) if pcfg["skip_blank_pages"] else None

    if mode.startswith("AI"):
        if st.session_state.get("starts_key") != key:
            st.session_state["starts_key"], st.session_state["starts"] = key, None
        if st.button("Nhận diện ranh giới hồ sơ"):
            with st.spinner("AI đang xem các trang..."):
                client = anthropic.Anthropic(api_key=api_key)
                pages = [i for i in range(n) if not (blank and blank[i])]
                st.session_state["starts"] = detect_patient_starts(client, cfg, pdf, pages)
        starts = st.session_state.get("starts")
        groups = pdfmod.split_by_starts(n, starts, blank) if starts else []
    else:
        groups = pdfmod.split_fixed(n, int(per), blank, pcfg["skip_blank_pages"])

    st.write(f"**{n} trang → {len(groups)} hồ sơ**" + (f" (bỏ {sum(blank)} trang trắng)" if blank else ""))
    if groups:
        st.caption(" · ".join(f"HS {i + 1}: tr. {g[0] + 1}–{g[-1] + 1}" for i, g in enumerate(groups)))

    if groups and st.button("Đọc hồ sơ", type="primary"):
        if not api_key:
            st.error("Nhập API key ở thanh bên.")
            st.stop()
        client = anthropic.Anthropic(api_key=api_key)
        bar = st.progress(0.0, "Đang đọc...")
        res = process_all(client, cfg, tpl, lk, pdf, groups,
                          progress=lambda d, t: bar.progress(d / t, f"Đã đọc {d}/{t} hồ sơ"))
        bar.empty()
        st.session_state.update(results=res, pdf=pdf, pdf_key=key, xlsx=None)

# ------------------------------------------------------------------ review
results = st.session_state.get("results")
if results:
    st.divider()
    st.subheader("Kiểm tra & sửa trước khi xuất")
    st.dataframe(pd.DataFrame([{
        "#": i + 1, "Họ tên": r.values.get("ho_ten", ""), "Ngày sinh": r.values.get("ngay_sinh", ""),
        "Trang": f"{r.pages[0] + 1}–{r.pages[-1] + 1}", "Số cảnh báo": len(r.flags),
        "Lỗi": r.error, "Ghi chú AI": r.notes} for i, r in enumerate(results)]),
        hide_index=True, use_container_width=True)

    idx = st.selectbox("Chọn hồ sơ", range(len(results)),
                       format_func=lambda i: f"{i + 1}. {results[i].values.get('ho_ten') or '(lỗi/chưa đọc được)'}")
    r = results[idx]
    if r.error:
        st.error(r.error)
    else:
        left, right = st.columns([5, 6])
        with left:
            pg = st.select_slider("Trang", options=[p + 1 for p in r.pages], key=f"pg{idx}")
            st.image(page_img(st.session_state["pdf_key"], pg - 1, st.session_state["pdf"]), use_container_width=True)
        with right:
            only_flag = st.checkbox("Chỉ hiện ô cần kiểm tra", value=True, key=f"of{idx}")
            rows = []
            for c in tpl.columns:
                if c.keyword not in r.values:
                    continue
                fl = "; ".join(r.flags.get(c.keyword, []))
                if only_flag and not fl:
                    continue
                rows.append({"Cột": f"{c.letter} · {c.label}", "keyword": c.keyword,
                             "Giá trị": r.values[c.keyword], "Cảnh báo": fl})
            df = pd.DataFrame(rows, columns=["Cột", "keyword", "Giá trị", "Cảnh báo"])
            edited = st.data_editor(df, hide_index=True, use_container_width=True, height=620,
                                    disabled=["Cột", "keyword", "Cảnh báo"], key=f"ed{idx}{only_flag}")
            for _, row in edited.iterrows():
                r.values[row["keyword"]] = "" if pd.isna(row["Giá trị"]) else str(row["Giá trị"])

    st.divider()
    if st.button("Tạo file Excel"):
        with st.spinner("Đang ghi file..."):
            st.session_state["xlsx"] = export_xlsx(cfg, tpl, lk, results)
    if st.session_state.get("xlsx"):
        st.download_button("⬇️ Tải file Excel import Medinet", st.session_state["xlsx"],
                           file_name="Import_M3_" + pd.Timestamp.now().strftime("%Y%m%d_%H%M") + ".xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

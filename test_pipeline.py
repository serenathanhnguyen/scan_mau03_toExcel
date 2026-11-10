"""Chạy:  python -m pytest tests -q     (không cần API key: dùng đầu ra AI giả lập)"""
import io, json, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m03.config import load_config                      # noqa: E402
from m03.pipeline import export_xlsx, finalize_result, schema_report  # noqa: E402
from m03.template import Template                       # noqa: E402

TPL = ROOT / "templates" / "Template_import_M3_V13.xlsx"
RAW = json.loads((ROOT / "tests/fixtures/mock_patient1.json").read_text(encoding="utf-8"))


def setup():
    cfg = load_config()
    tpl = Template(TPL.read_bytes(), cfg.settings)
    return cfg, tpl, tpl.lookups()


def test_schema_in_sync():
    cfg, tpl, _ = setup()
    rep = schema_report(cfg, tpl)
    assert not rep["new_columns"], [c.keyword for c in rep["new_columns"]]
    assert not rep["removed_keywords"]


def test_finalize_values():
    cfg, tpl, lk = setup()
    r = finalize_result(cfg, tpl, lk, RAW, [0])
    v = r.values
    assert v["ho_ten"] == "MAI PHƯƠNG VY" and v["ngay_sinh"] == "11/05/2008" and v["gioi_tinh"] == "2"
    assert v["dinh_danh_ca_nhan"] == "068308012959" and v["sdt"] == "0798365444"
    assert v["city_id"] == "Thành Phố Hồ Chí Minh" and v["ward_id"].lower().endswith("phú thuận")
    assert v["nghenghiep_code"] == "Lao động tự do"            # Stylist không có trong danh mục -> mặc định
    assert v["kskdk_xnm_huyetsacto"] == "123" and v["kskdk_xnm_hematocrit"] == "0,372"
    assert v["hohap_chandoansobo_icd"] == "J32.8" and v["hohap_chuaphathienbatthuong"] == "0"
    assert v["mat_khongkinh_mp"] == "10" and v["tai_noitham_tt"] == "1,5"
    assert v["benh_khac"] == "Không" and v["loai_kham"] == "2" and v["can_lam_sang_khac"] == "2"
    assert v["kskdk_xnnt_nitrit"] == "1"
    assert v["hinh_thuc_chi_tra_khamsk"] == "Người sử dụng lao động chi trả"


def test_build_xlsx_roundtrip():
    import openpyxl, warnings
    warnings.simplefilter("ignore")
    cfg, tpl, lk = setup()
    r = finalize_result(cfg, tpl, lk, RAW, [0])
    data = export_xlsx(cfg, tpl, lk, [r, r])
    zf = zipfile.ZipFile(io.BytesIO(data)); assert zf.testzip() is None
    orig = zipfile.ZipFile(io.BytesIO(TPL.read_bytes()))
    assert set(zf.namelist()) == set(orig.namelist())           # giữ nguyên bảng, dropdown, công thức...
    changed = {n for n in zf.namelist() if zf.read(n) != orig.read(n)}
    assert changed == {tpl.sheet_path, "xl/sharedStrings.xml", "xl/workbook.xml"}, changed
    ws = openpyxl.load_workbook(io.BytesIO(data))["ThongTinHanhChinh"]
    col = {c.keyword: c.letter for c in tpl.columns}
    assert ws[f"{col['ho_ten']}5"].value == "MAI PHƯƠNG VY" and ws[f"{col['ho_ten']}6"].value == "MAI PHƯƠNG VY"
    assert ws["A6"].value == 2
    assert ws["GE5"].value.startswith("=IFERROR(VLOOKUP(R5")          # công thức còn nguyên
    assert ws[f"{col['dinh_danh_ca_nhan']}7"].value in (None, "")     # dòng 7 không có dữ liệu


def test_new_column_detected_and_filled_from_sample(tmp_path):
    """Giả lập Medinet thêm cột: xoá khai báo 'sdt' khỏi yaml -> tool phải báo cột mới và điền theo dòng 5."""
    import shutil, yaml
    from m03.config import CONFIG_DIR
    shutil.copytree(CONFIG_DIR, tmp_path / "c")
    y = yaml.safe_load((tmp_path / "c/fields.yaml").read_text(encoding="utf-8"))
    del y["fields"]["sdt"]
    (tmp_path / "c/fields.yaml").write_text(yaml.safe_dump(y, allow_unicode=True), encoding="utf-8")
    cfg = load_config(tmp_path / "c")
    tpl = Template(TPL.read_bytes(), cfg.settings)
    assert [c.keyword for c in schema_report(cfg, tpl)["new_columns"]] == ["sdt"]
    r = finalize_result(cfg, tpl, tpl.lookups(), RAW, [0])
    assert r.values["sdt"] == tpl.sample["sdt"] == "0963642456"

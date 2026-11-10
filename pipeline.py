"""Ghép các bước: PDF -> nhóm trang -> AI -> chuẩn hoá -> Excel."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .config import Config
from .template import Template, compute_formula_cache
from .transform import Lookups, finalize


@dataclass
class PatientResult:
    pages: list[int]
    raw: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)      # {keyword: str}  (người dùng có thể sửa)
    flags: dict = field(default_factory=dict)       # {keyword: [cảnh báo]}
    notes: str = ""
    error: str = ""


def schema_report(cfg: Config, tpl: Template) -> dict:
    """So khớp file mẫu với fields.yaml -> báo cột mới/cột đã bỏ để người dùng biết cần sửa gì."""
    declared = {k for k, f in cfg.fields.items() if not f.virtual}
    return {
        "new_columns": [c for c in tpl.columns if c.keyword not in declared],
        "removed_keywords": sorted(declared - set(tpl.keywords)),
        "n_columns": len(tpl.columns),
    }


def finalize_result(cfg: Config, tpl: Template, lk: Lookups, raw: dict, pages: list[int]) -> PatientResult:
    values, flags = finalize(cfg, raw, tpl.sample, set(tpl.keywords), lk, tpl.required)
    return PatientResult(pages=pages, raw=raw, values=values, flags=flags, notes=raw.get("_notes") or "")


def process_all(client, cfg: Config, tpl: Template, lk: Lookups, pdf: bytes, groups: list[list[int]],
                progress=None, extractor=None) -> list[PatientResult]:
    from .extract import extract_patient
    extractor = extractor or (lambda pages: extract_patient(client, cfg, pdf, pages, set(tpl.keywords)))
    results: list[PatientResult | None] = [None] * len(groups)

    def work(i):
        try:
            return i, finalize_result(cfg, tpl, lk, extractor(groups[i]), groups[i])
        except Exception as e:  # noqa: BLE001 - hiển thị lỗi theo từng bệnh nhân
            return i, PatientResult(pages=groups[i], error=f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(cfg.settings["claude"].get("workers", 3)) as ex:
        for done, (i, r) in enumerate(ex.map(work, range(len(groups))), 1):
            results[i] = r
            if progress:
                progress(done, len(groups))
    return results  # type: ignore[return-value]


def export_xlsx(cfg: Config, tpl: Template, lk: Lookups, results: list[PatientResult]) -> bytes:
    ok = [r for r in results if not r.error]
    cache = [compute_formula_cache(cfg.settings, r.values, lk) for r in ok]
    return tpl.build([r.values for r in ok], cache)

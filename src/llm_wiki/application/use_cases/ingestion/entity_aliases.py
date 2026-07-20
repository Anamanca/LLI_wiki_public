"""Canonical entity name mappings for coreference resolution in merge phase.

Maps common aliases/abbreviations to a single canonical form.
Add new mappings as they're discovered in transcripts.
"""

CANONICAL_ALIASES: dict[str, str] = {
    # Vietnamese finance institutions
    "nhnn": "ngan_hang_nha_nuoc_viet_nam",
    "ngân hàng nhà nước": "ngan_hang_nha_nuoc_viet_nam",
    "ngân hàng nhà nước việt nam": "ngan_hang_nha_nuoc_viet_nam",
    "sbv": "ngan_hang_nha_nuoc_viet_nam",
    "state bank of vietnam": "ngan_hang_nha_nuoc_viet_nam",
    "uỷ ban chứng khoán": "uy_ban_chung_khoan_nha_nuoc",
    "uỷ ban chứng khoán nhà nước": "uy_ban_chung_khoan_nha_nuoc",
    "ubck": "uy_ban_chung_khoan_nha_nuoc",
    "ssc": "uy_ban_chung_khoan_nha_nuoc",
    "bộ tài chính": "bo_tai_chinh",
    "mof": "bo_tai_chinh",
    "tổng cục thống kê": "tong_cuc_thong_ke",
    "gso": "tong_cuc_thong_ke",
    # International institutions
    "fed": "federal_reserve",
    "cục dự trữ liên bang mỹ": "federal_reserve",
    "cục dự trữ liên bang": "federal_reserve",
    "federal reserve": "federal_reserve",
    "ecb": "european_central_bank",
    "ngân hàng trung ương châu âu": "european_central_bank",
    "imf": "international_monetary_fund",
    "quỹ tiền tệ quốc tế": "international_monetary_fund",
    "wb": "world_bank",
    "ngân hàng thế giới": "world_bank",
    "world bank": "world_bank",
    "opec": "opec",
    # Market indices
    "vn-index": "vn_index",
    "vnindex": "vn_index",
    "vn index": "vn_index",
    "hnx-index": "hnx_index",
    "hnxindex": "hnx_index",
    "s&p 500": "sp500",
    "s&p500": "sp500",
    "dow jones": "dow_jones",
    "nasdaq": "nasdaq",
    "nikkei": "nikkei_225",
    "shanghai composite": "shanghai_composite",
    "hang seng": "hang_seng",
    # Common abbreviations
    "ck": "chung_khoan",
    "bđs": "bat_dong_san",
    "nh": "ngan_hang",
    "ctck": "cong_ty_chung_khoan",
    "tckh": "thi_truong_chung_khoan",
}


def canonicalize(name: str) -> str:
    """Return canonical form of entity name, or original if no mapping."""
    key = name.lower().strip()
    # Remove special chars for more flexible matching
    key = key.replace(".", "").replace(",", "")
    return CANONICAL_ALIASES.get(key, key)

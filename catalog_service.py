"""Effective product catalog source layer for Lapakin Asisten.

Rules:
- source=lapakin: product catalog is read from Lapakin context via lapakin_shop_id.
- source=standalone: product catalog is read from ai_wa_bot.products.
- callers should use effective products, not raw local products only.
"""
from typing import Any, Dict, List, Optional

from deps import db
from context_service import get_shop_context

try:
    from reply_rules import _extract_products as _reply_extract_products
except Exception:
    _reply_extract_products = None


LOCAL_CATALOG_SOURCE = "local"
LAPAKIN_CATALOG_SOURCE = "lapakin"


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, dict)]


def _product_name(product: dict) -> str:
    return str(
        product.get("name")
        or product.get("title")
        or product.get("product_name")
        or product.get("nama")
        or ""
    ).strip()


def _is_product_active(product: dict) -> bool:
    if product.get("is_active") is False:
        return False

    if product.get("active") is False:
        return False

    status = str(product.get("status") or product.get("product_status") or "").lower().strip()
    if status in {"inactive", "deleted", "draft", "hidden", "archived", "nonactive", "nonaktif"}:
        return False

    return bool(_product_name(product))


def _normalize_product(product: dict, source: str) -> dict:
    item = dict(product)

    if not item.get("name"):
        item["name"] = (
            item.get("title")
            or item.get("product_name")
            or item.get("nama")
            or ""
        )

    if item.get("price") is None:
        item["price"] = (
            item.get("selling_price")
            or item.get("sale_price")
            or item.get("harga")
            or item.get("amount")
        )

    if not item.get("product_id"):
        item["product_id"] = (
            item.get("id")
            or item.get("sku")
            or item.get("slug")
            or _product_name(item)
        )

    item["catalog_source"] = source
    return item


def extract_products_from_context(context: dict) -> List[dict]:
    """Extract products from any supported context shape."""
    if not isinstance(context, dict):
        return []

    if _reply_extract_products:
        try:
            products = _reply_extract_products(context)
            if products:
                return _as_dict_list(products)
        except Exception:
            pass

    candidate_paths = [
        ["products"],
        ["effective_products"],
        ["catalog", "products"],
        ["shop", "products"],
        ["store", "products"],
        ["lapakin", "products"],
        ["lapakin_context", "products"],
    ]

    for path in candidate_paths:
        cur: Any = context
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)

        products = _as_dict_list(cur)
        if products:
            return products

    return []


async def get_shop_doc(shop_id: str) -> dict:
    return await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}


def get_catalog_source_from_shop(shop: dict) -> str:
    source = str(shop.get("catalog_source") or shop.get("source") or "standalone").lower()

    if source == "lapakin" or shop.get("lapakin_shop_id"):
        return LAPAKIN_CATALOG_SOURCE

    return LOCAL_CATALOG_SOURCE


async def get_local_products(shop_id: str) -> List[dict]:
    rows = await db.products.find(
        {"shop_id": shop_id},
        {"_id": 0},
    ).to_list(1000)

    return [
        _normalize_product(p, LOCAL_CATALOG_SOURCE)
        for p in rows
        if isinstance(p, dict) and _is_product_active(p)
    ]


async def get_lapakin_products(shop_id: str, context: Optional[dict] = None) -> List[dict]:
    if context is None:
        context = await get_shop_context(shop_id) or {}

    rows = extract_products_from_context(context)

    return [
        _normalize_product(p, LAPAKIN_CATALOG_SOURCE)
        for p in rows
        if isinstance(p, dict) and _is_product_active(p)
    ]


async def get_effective_products(shop_id: str, context: Optional[dict] = None) -> List[dict]:
    shop = await get_shop_doc(shop_id)
    catalog_source = get_catalog_source_from_shop(shop)

    if catalog_source == LAPAKIN_CATALOG_SOURCE:
        products = await get_lapakin_products(shop_id, context=context)

        # Dev fallback only: if Lapakin context is temporarily unavailable but
        # local cache exists, still return local products. Source info will show fallback.
        if products:
            return products

        local = await get_local_products(shop_id)
        return local

    return await get_local_products(shop_id)


async def get_catalog_source_info(shop_id: str, context: Optional[dict] = None) -> dict:
    shop = await get_shop_doc(shop_id)
    catalog_source = get_catalog_source_from_shop(shop)
    products = await get_effective_products(shop_id, context=context)

    if catalog_source == LAPAKIN_CATALOG_SOURCE:
        label = "LapakinUMKM"
        detail = f"Produk diambil dari LapakinUMKM lapakin_shop_id={shop.get('lapakin_shop_id') or '-'}"
    else:
        label = "Lapakin Asisten"
        detail = "Produk diambil dari database lokal Lapakin Asisten"

    actual_sources = sorted(set([p.get("catalog_source") for p in products if p.get("catalog_source")]))

    return {
        "shop_id": shop_id,
        "shop_source": shop.get("source"),
        "catalog_source": catalog_source,
        "catalog_source_label": label,
        "catalog_source_detail": detail,
        "lapakin_shop_id": shop.get("lapakin_shop_id"),
        "effective_product_count": len(products),
        "actual_product_sources": actual_sources,
    }


async def enrich_context_with_effective_products(shop_id: str, context: Optional[dict] = None) -> dict:
    if not isinstance(context, dict):
        context = {}

    enriched = dict(context)
    products = await get_effective_products(shop_id, context=enriched)
    source_info = await get_catalog_source_info(shop_id, context=enriched)

    enriched["products"] = products
    enriched["effective_products"] = products
    enriched["catalog_source_info"] = source_info

    return enriched

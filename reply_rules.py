"""Deterministic reply rules for AI WA Bot.

This module runs before the LLM so common commerce questions can be answered
from trusted shop/product context without hallucinating price, stock, or payment info.
"""
import re
from typing import Any, Dict, List, Optional, Tuple


STOPWORDS = {
    "apa", "aja", "saja", "yang", "ini", "itu", "kak", "ka", "bang", "mbak",
    "mas", "pak", "bu", "berapa", "harga", "ada", "stok", "ready", "mau",
    "pesan", "beli", "order", "kalau", "kalo", "untuk", "porsi", "botol",
    "pcs", "buah", "menu", "produk", "jual", "jualan", "tersedia",
}

NUMBER_WORDS = {
    "satu": 1,
    "se": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
    "sepuluh": 10,
}


def build_rule_reply(
    message: str,
    context: Dict[str, Any],
    session_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return a rule-based reply payload or None if LLM should handle it."""
    session_doc = session_doc or {}
    msg_norm = _normalize(message)
    products = _extract_products(context)

    if _is_product_list_question(msg_norm):
        return _reply_product_list(products, context)

    recommendation = _reply_smart_recommendation(
        original_message=message,
        msg_norm=msg_norm,
        products=products,
        context=context,
        session_doc=session_doc,
    )
    if recommendation:
        return recommendation

    if _is_payment_question(msg_norm):
        payment = _extract_payment_text(context)
        if payment:
            return {
                "reply": f"Bisa kak. Info pembayaran: {payment}",
                "intent": "payment_inquiry",
                "confidence": "high",
                "source": "rule_payment",
                "handoff_required": False,
                "session_update": {},
            }

    if _is_hours_question(msg_norm):
        hours = _extract_hours_text(context)
        if hours:
            return {
                "reply": f"Jam buka toko: {hours}",
                "intent": "hours_inquiry",
                "confidence": "high",
                "source": "rule_hours",
                "handoff_required": False,
                "session_update": {},
            }

    if _is_location_question(msg_norm):
        address = _extract_address_text(context)
        if address:
            return {
                "reply": f"Lokasi toko di {address} kak.",
                "intent": "location_inquiry",
                "confidence": "high",
                "source": "rule_location",
                "handoff_required": False,
                "session_update": {},
            }

    matched_product, match_score = _match_product(message, products)
    remembered_product = _get_remembered_product(session_doc, products)
    quantity = _extract_quantity(msg_norm)

    product = matched_product

    # Follow-up questions like "kalau 2 berapa?" should use the last product
    # from the same session if no product is explicitly mentioned.
    if not product and remembered_product and _looks_like_followup_price_or_order(msg_norm, quantity):
        product = remembered_product
        match_score = 0.51

    if not product:
        return None

    product_name = product.get("name") or "produk tersebut"
    price = _to_number(product.get("price"))
    stock_status = _stock_status(product)
    session_update = {
        "current_product": {
            "product_id": product.get("id") or product.get("product_id"),
            "name": product_name,
            "price": price,
            "category": product.get("category"),
            "updated_from": "reply_rules",
        }
    }

    if quantity and price and (_is_price_question(msg_norm) or _is_order_intent(msg_norm) or _looks_like_followup_price_or_order(msg_norm, quantity)):
        total = int(price * quantity)
        return {
            "reply": _sales_product_reply(product, mode="quantity_total", quantity=quantity, total=total),
            "intent": "price_inquiry",
            "confidence": "high",
            "source": "rule_product_memory" if not matched_product else "rule_product",
            "handoff_required": False,
            "session_update": session_update,
            "product_card": _build_product_card(product),
        }

    if _is_price_question(msg_norm) and price:
        return {
            "reply": _sales_product_reply(product, mode="price"),
            "intent": "price_inquiry",
            "confidence": "high",
            "source": "rule_product",
            "handoff_required": False,
            "session_update": session_update,
            "product_card": _build_product_card(product),
        }

    if _is_stock_question(msg_norm):
        if stock_status == "out":
            reply = f"Mohon maaf kak, {product_name} saat ini tercatat belum tersedia."
            handoff = False
        elif price:
            reply = (
                f"{product_name} ada di katalog dengan harga {_format_rupiah(price)} kak. "
                "Untuk stok real-time, saya bantu konfirmasi ke owner ya."
            )
            handoff = True
        else:
            reply = (
                f"{product_name} ada di katalog kak. Untuk harga/stok real-time, "
                "saya bantu konfirmasi ke owner ya."
            )
            handoff = True

        return {
            "reply": reply,
            "intent": "stock_inquiry",
            "confidence": "high" if match_score >= 0.5 else "medium",
            "source": "rule_product",
            "handoff_required": handoff,
            "session_update": session_update,
            "product_card": _build_product_card(product),
        }

    if _is_order_intent(msg_norm):
        if price:
            reply = _sales_product_reply(product, mode="order")
        else:
            reply = _sales_product_reply(product, mode="order")
        return {
            "reply": reply,
            "intent": "order_intent",
            "confidence": "high" if match_score >= 0.5 else "medium",
            "source": "rule_product",
            "handoff_required": False,
            "session_update": session_update,
            "product_card": _build_product_card(product),
        }

    if price:
        return {
            "reply": _sales_product_reply(product, mode="inquiry"),
            "intent": "product_inquiry",
            "confidence": "high" if match_score >= 0.5 else "medium",
            "source": "rule_product",
            "handoff_required": False,
            "session_update": session_update,
            "product_card": _build_product_card(product),
        }

    return {
        "reply": _sales_product_reply(product, mode="inquiry"),
        "intent": "product_inquiry",
        "confidence": "medium",
        "source": "rule_product",
        "handoff_required": True,
        "session_update": session_update,
        "product_card": _build_product_card(product),
    }





def _sales_clean(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""

    if len(text) <= limit:
        return text

    cut = text[:limit].rstrip()

    # Prefer ending at a complete sentence if available.
    sentence_end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if sentence_end >= 80:
        cut = cut[:sentence_end + 1].strip()
    else:
        # Otherwise cut at the last safe word boundary.
        last_space = cut.rfind(" ")
        if last_space >= 80:
            cut = cut[:last_space].strip()

        # Avoid dangling connector words at the end.
        cut = re.sub(r"\b(atau|dan|untuk|dengan|pada|di|ke|yang|agar|sebagai)$", "", cut, flags=re.IGNORECASE).strip(" ,.-")

        if not cut.endswith((".", "!", "?")):
            cut += "..."

    return cut

def _sales_price(product: Dict[str, Any]) -> str:
    price = _to_number(product.get("price"))
    label = _sales_clean(product.get("price_label"), 80)
    if price:
        return _format_rupiah(price)
    return label or "harga perlu dikonfirmasi admin"


def _sales_text(product: Dict[str, Any]) -> str:
    parts = [
        product.get("name"),
        product.get("category"),
        product.get("category_name"),
        product.get("product_type"),
    ]
    return _normalize(" ".join(str(x) for x in parts if x))

def _sales_value(product: Dict[str, Any]) -> str:
    desc = _sales_clean(product.get("short_description") or product.get("description"), 150)
    if desc:
        return f"Detail singkatnya: {desc}"

    text = _sales_text(product)
    if any(k in text for k in ["clicker", "fidget", "keychain", "gantungan"]):
        return "Produk ini cocok untuk fidget kecil, gantungan tas/kunci, atau hadiah lucu yang ringan."
    if any(k in text for k in ["lamp", "lampu", "table lamp"]):
        return "Produk ini cocok untuk dekorasi meja, hadiah unik, atau item display yang lebih standout."
    if _sales_is_custom_product(product):
        return "Ini cocok kalau kakak punya ide, referensi, atau model tertentu yang ingin dibuatkan."
    if any(k in text for k in ["gift", "souvenir", "hadiah", "kado"]):
        return "Produk ini cocok untuk hadiah personal, souvenir kecil, atau kebutuhan custom gift."
    return "Saya bisa bantu arahkan pilihan yang paling cocok sesuai kebutuhan kakak."

def _sales_is_custom_product(product: Dict[str, Any]) -> bool:
    name = _normalize(product.get("name"))
    category = _normalize(product.get("category") or product.get("category_name"))
    product_type = _normalize(product.get("product_type"))

    strong_text = " ".join([name, category, product_type])

    if any(k in name for k in ["custom 3d print", "custom gift", "custom souvenir"]):
        return True
    if product_type in {"preorder_print", "custom", "custom_print", "made_to_order"}:
        return True
    if any(k in category for k in ["custom-3d", "custom 3d", "custom-print", "custom print"]):
        return True
    if "custom" in strong_text and not any(k in strong_text for k in ["clicker", "keychain", "gantungan", "fidget"]):
        return True

    return False

def _sales_followup(product: Dict[str, Any], mode: str = "inquiry") -> str:
    text = _sales_text(product)

    # Specific ready-stock product types must win over generic "custom" keywords.
    if any(k in text for k in ["clicker", "fidget", "keychain", "gantungan"]):
        return "Kakak mau saya bantu bandingkan dengan varian clicker lain, atau mau lanjut lihat detail produk ini?"

    if any(k in text for k in ["lamp", "lampu", "table lamp"]):
        return "Kakak mau dipakai untuk dekorasi sendiri atau untuk hadiah?"

    if _sales_is_custom_product(product):
        return "Kakak sudah punya file/desain, atau masih berupa gambar referensi?"

    if mode == "order":
        return "Kakak mau ambil berapa pcs?"

    return "Kakak mau saya bantu pilihkan opsi yang paling cocok, atau mau lanjut ke produk ini?"

def _sales_product_reply(product: Dict[str, Any], mode: str = "inquiry", quantity: Optional[int] = None, total: Optional[int] = None) -> str:
    name = product.get("name") or "produk tersebut"
    price = _to_number(product.get("price"))
    price_text = _sales_price(product)
    value = _sales_value(product)
    followup = _sales_followup(product, mode)

    if mode == "quantity_total" and quantity and total is not None:
        return (
            f"Siap kak. Untuk {quantity} {name}, total sementaranya {_format_rupiah(total)}.\n\n"
            f"{value}\n\n"
            "Mau saya bantu teruskan sebagai pesanan, atau kakak mau cek varian lain dulu?"
        )

    if mode == "price":
        return f"{name} harganya {price_text} kak.\n\n{value}\n\n{followup}"

    if mode == "order":
        if price:
            return f"Siap kak, {name} bisa dibantu. Harganya {price_text}.\n\n{value}\n\n{followup}"
        return f"Siap kak, {name} bisa dibantu. Untuk harga/detail paling akurat, saya bantu konfirmasi ke owner ya.\n\n{value}\n\n{followup}"

    if price:
        return f"Ada kak, {name} tersedia di katalog. Harganya {price_text}.\n\n{value}\n\n{followup}"

    return f"Ada kak, {name} masuk katalog. Untuk harga/detail paling akurat, saya bantu konfirmasi ke owner ya.\n\n{value}\n\n{followup}"


def _sales_group_key(product: Dict[str, Any]) -> str:
    text = _sales_text(product)

    # Put concrete ready-stock products first, so clickers/keychains do not get swallowed by generic custom keywords.
    if any(k in text for k in ["clicker", "fidget", "keychain", "gantungan"]):
        return "clicker"
    if any(k in text for k in ["lamp", "lampu", "table lamp"]):
        return "lamp"
    if _sales_is_custom_product(product):
        return "custom"
    if any(k in text for k in ["gift", "souvenir", "hadiah", "kado"]):
        return "gift"
    return "ready"

def _sales_chip(product: Dict[str, Any]) -> str:
    name = product.get("name") or "Produk"
    price = _to_number(product.get("price"))
    return f"{name} ({_format_rupiah(price)})" if price else name


def _build_product_card(product: Dict[str, Any]) -> Dict[str, Any]:
    price = _to_number(product.get("price"))
    price_label = product.get("price_label")
    if not price_label:
        price_label = _format_rupiah(price) if price else "Harga perlu konfirmasi admin"

    return {
        "product_id": product.get("id") or product.get("product_id"),
        "name": product.get("name") or "Produk SpaceCraft",
        "price": price,
        "price_label": price_label,
        "image_url": product.get("image_url") or product.get("image"),
        "product_url": product.get("product_url") or product.get("url"),
        "product_type": product.get("product_type"),
        "category": product.get("category") or product.get("category_name"),
    }


def _reply_product_list(products: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[str, Any]:
    active = [p for p in products if _is_product_active(p)]

    if not active:
        return {
            "reply": "Maaf kak, katalog produk belum tersedia. Saya bantu teruskan ke owner ya.",
            "intent": "product_list",
            "confidence": "high",
            "source": "rule_product_list",
            "handoff_required": True,
            "session_update": {},
        }

    shop_name = _extract_shop_name(context)
    prefix = f"Siap kak. Di {shop_name}, produknya bisa saya bantu pilihkan berdasarkan kebutuhan:" if shop_name else "Siap kak. Produknya bisa saya bantu pilihkan berdasarkan kebutuhan:"

    labels = {
        "custom": "Custom 3D print",
        "gift": "Gift & souvenir",
        "lamp": "Table lamp/dekorasi",
        "clicker": "Clicker/keychain lucu",
        "ready": "Produk ready stock",
    }
    order = ["custom", "gift", "lamp", "clicker", "ready"]
    grouped = {key: [] for key in order}

    for product in active:
        grouped.setdefault(_sales_group_key(product), []).append(product)

    lines = []
    for key in order:
        items = grouped.get(key) or []
        if not items:
            continue
        examples = ", ".join(_sales_chip(x) for x in items[:2])
        if len(items) > 2:
            examples += f", dan {len(items) - 2} lainnya"
        lines.append(f"- {labels.get(key, 'Produk')}: {examples}")

    if not lines:
        examples = ", ".join(_sales_chip(x) for x in active[:4])
        if len(active) > 4:
            examples += f", dan {len(active) - 4} lainnya"
        lines = [f"- Produk tersedia: {examples}"]

    closing = "Kakak lagi cari untuk hadiah, koleksi pribadi, fidget/keychain, table lamp, atau mau custom model tertentu?"

    return {
        "reply": prefix + "\n" + "\n".join(lines[:5]) + "\n\n" + closing,
        "intent": "product_list",
        "confidence": "high",
        "source": "rule_product_list",
        "handoff_required": False,
        "session_update": {},
    }


RECOMMENDATION_NEED_TERMS = {
    "gift": {
        "hadiah", "kado", "souvenir", "gift", "lucu", "unik", "cute",
        "buat anak", "untuk anak", "koleksi", "temen", "teman", "pacar",
    },
    "fidget": {
        "fidget", "clicker", "keychain", "gantungan", "mainan", "pencet",
        "stress", "anti stress", "tas", "kunci",
    },
    "lamp": {
        "lampu", "lamp", "table lamp", "dekorasi", "meja", "kamar",
        "pendant", "hiasan",
    },
    "custom": {
        "custom", "buatkan", "bikinin", "bikin", "request", "karakter",
        "anime", "figure", "figur", "model", "3d print", "cetak", "desain sendiri",
    },
}


def _reply_smart_recommendation(
    *,
    original_message: str,
    msg_norm: str,
    products: List[Dict[str, Any]],
    context: Dict[str, Any],
    session_doc: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    active = [p for p in products if _is_product_active(p)]
    if not active:
        return None

    needs = _detect_recommendation_needs(msg_norm)
    if not needs:
        return None

    # If the customer clearly mentions a product name such as "Coffee Latte" or "Oreo",
    # let the normal product matcher handle it. Generic words like clicker/keychain do not count.
    if _has_specific_product_mention(original_message, active):
        return None

    ranked = _rank_products_for_needs(active, needs)
    if not ranked:
        return None

    selected = ranked[:3]
    reply = _build_recommendation_reply(selected, needs, context)

    session_update = {
        "recommendation_context": {
            "needs": needs,
            "recommended_product_ids": [
                p.get("id") or p.get("product_id")
                for p in selected
                if p.get("id") or p.get("product_id")
            ],
            "updated_from": "smart_recommendation",
        }
    }

    intent = "custom_request" if "custom" in needs else "product_recommendation"

    return {
        "reply": reply,
        "intent": intent,
        "confidence": "high",
        "source": "rule_recommendation",
        "handoff_required": False,
        "session_update": session_update,
    }


def _detect_recommendation_needs(msg_norm: str) -> List[str]:
    needs = []

    # Recommendation intent can be explicit or implied by broad category/use-case wording.
    has_reco_intent = any(term in msg_norm for term in [
        "rekomendasi", "rekomendasiin", "pilihin", "pilihkan", "saran",
        "yang cocok", "cocok", "ada yang", "punya yang", "buat ", "untuk ",
        "mau yang", "cari yang", "lihat yang",
    ])

    for need, terms in RECOMMENDATION_NEED_TERMS.items():
        if any(term in msg_norm for term in terms):
            needs.append(need)

    if not needs:
        return []

    # If the message only says a very specific product name, do not treat it as recommendation.
    # But category wording like "ada yang fidget clicker" should still be recommendation.
    if has_reco_intent:
        return _dedupe_needs(needs)

    # Strong category terms also count even without explicit "rekomendasi".
    if any(k in msg_norm for k in ["fidget", "clicker", "keychain", "gantungan", "lampu", "table lamp", "custom", "anime", "figure", "figur"]):
        return _dedupe_needs(needs)

    return []


def _dedupe_needs(needs: List[str]) -> List[str]:
    order = ["custom", "gift", "fidget", "lamp", "ready"]
    seen = set()
    result = []

    for item in needs:
        if item not in seen:
            seen.add(item)

    for item in order:
        if item in seen:
            result.append(item)

    return result


def _has_specific_product_mention(message: str, products: List[Dict[str, Any]]) -> bool:
    msg_norm = _normalize(message)
    msg_tokens = _tokens(msg_norm)

    generic = set(GENERIC_PRODUCT_MATCH_TERMS) | {
        "clicker", "fidget", "keychain", "gantungan", "lampu", "lamp",
        "custom", "hadiah", "kado", "souvenir", "lucu", "unik", "produk",
    }

    for product in products:
        name = _normalize(product.get("name"))
        if not name:
            continue

        name_tokens = _tokens(name)
        meaningful = [
            token for token in name_tokens
            if len(token) >= 4 and token not in generic and token not in STOPWORDS
        ]

        # Example: "coffee latte", "oreo", "squid", "tulip", "billow".
        if any(token in msg_tokens for token in meaningful):
            return True

        if len(meaningful) >= 2 and all(token in msg_tokens for token in meaningful[:2]):
            return True

    return False


def _recommendation_text(product: Dict[str, Any]) -> str:
    parts = [
        product.get("name"),
        product.get("category"),
        product.get("category_name"),
        product.get("product_type"),
        product.get("short_description"),
        product.get("description"),
        product.get("search_keywords"),
    ]
    return _normalize(" ".join(str(x) for x in parts if x))


def _rank_products_for_needs(products: List[Dict[str, Any]], needs: List[str]) -> List[Dict[str, Any]]:
    scored = []

    for product in products:
        text = _recommendation_text(product)
        price = _to_number(product.get("price"))
        score = 0.0

        if "custom" in needs:
            # Custom request should recommend only explicit custom service/products.
            # Do not include ordinary physical/preorder items like bowls, lamps, or ready stock products.
            custom_text = _normalize(" ".join(str(x or "") for x in [
                product.get("name"),
                product.get("category"),
                product.get("category_name"),
                product.get("slug"),
                product.get("sku"),
            ]))

            is_custom_service = any(k in custom_text for k in [
                "custom 3d print",
                "custom gift",
                "custom souvenir",
                "custom-print",
                "custom print",
                "custom",
            ])

            if not is_custom_service:
                continue

            score += 10

        if "gift" in needs:
            if any(k in text for k in ["gift", "hadiah", "kado", "souvenir"]):
                score += 4
            if any(k in text for k in ["clicker", "fidget", "keychain", "gantungan", "cute", "lucu", "oreo", "coffee", "squid"]):
                score += 3
            if price and price <= 25000:
                score += 1.5

        if "fidget" in needs:
            if any(k in text for k in ["clicker", "fidget"]):
                score += 6
            if any(k in text for k in ["keychain", "gantungan"]):
                score += 2
            if price and price <= 25000:
                score += 1

        if "lamp" in needs:
            if any(k in text for k in ["lamp", "lampu", "table lamp", "pendant", "dekorasi"]):
                score += 7

        if product.get("is_recommended"):
            score += 0.5

        if score > 0:
            scored.append((score, _recommendation_tiebreaker(product), product))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored]


def _recommendation_tiebreaker(product: Dict[str, Any]) -> float:
    price = _to_number(product.get("price"))
    return float(price or 999999999)


def _recommendation_reason(product: Dict[str, Any], needs: List[str]) -> str:
    text = _recommendation_text(product)
    name = _normalize(product.get("name"))

    if "custom" in needs:
        if "3d print" in name or "print" in name:
            return "untuk bikin model dari referensi, gambar, atau file 3D"
        if "gift" in name or "souvenir" in name:
            return "untuk hadiah/souvenir custom dengan konsep tertentu"
        return "untuk kebutuhan custom yang perlu dicek detail ukurannya"

    if "lamp" in needs and any(k in text for k in ["lamp", "lampu", "table lamp", "pendant"]):
        return "cocok untuk dekorasi meja/kamar atau hadiah yang lebih standout"
    if "fidget" in needs and any(k in text for k in ["clicker", "fidget"]):
        return "cocok untuk fidget kecil dan gantungan yang enak dibawa"
    if "gift" in needs:
        if "coffee" in name or "latte" in name:
            return "cocok untuk pecinta kopi"
        if "oreo" in name:
            return "lucu untuk hadiah ringan dengan budget hemat"
        if "squid" in name or "cute" in name:
            return "lebih unik dan playful untuk koleksi atau hadiah"
        if "lipbalm" in name:
            return "praktis sebagai gantungan kecil yang fungsional"
        return "cocok untuk hadiah kecil yang personal"

    return "opsi yang relevan dengan kebutuhan kakak"


def _build_recommendation_reply(products: List[Dict[str, Any]], needs: List[str], context: Dict[str, Any]) -> str:
    shop_name = _extract_shop_name(context)

    if "custom" in needs:
        opener = "Bisa kak. Untuk custom karakter/model, opsi awalnya:"
        closing = "Kakak sudah punya gambar/file referensi, atau masih berupa ide kasar?"
    elif "lamp" in needs:
        opener = "Ada kak. Untuk lampu/dekorasi, saya rekomendasikan opsi ini:"
        closing = "Kakak cari untuk meja kerja, kamar, atau hadiah?"
    elif "fidget" in needs:
        opener = "Ada kak. Untuk fidget/clicker, pilihan yang paling cocok:"
        closing = "Kakak mau yang budget hemat, yang paling lucu, atau yang bentuknya lebih unik?"
    elif "gift" in needs:
        opener = "Bisa kak. Untuk hadiah yang lucu, saya rekomendasikan ini:"
        closing = "Kakak mau yang budget hemat, paling lucu, atau yang terlihat lebih premium?"
    else:
        opener = f"Siap kak. Di {shop_name}, saya rekomendasikan ini:" if shop_name else "Siap kak. Saya rekomendasikan ini:"
        closing = "Kakak mau saya bantu pilihkan satu yang paling cocok?"

    lines = []
    for idx, product in enumerate(products, 1):
        name = product.get("name") or f"Produk {idx}"
        price = _to_number(product.get("price"))
        price_text = _format_rupiah(price) if price else (product.get("price_label") or "harga konfirmasi admin")
        reason = _recommendation_reason(product, needs)
        lines.append(f"{idx}. {name} — {price_text}, {reason}.")

    return opener + "\n" + "\n".join(lines) + "\n\n" + closing


def _extract_products(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Any] = []

    def add(value: Any):
        if isinstance(value, list):
            candidates.extend(value)

    add(context.get("products"))
    add(context.get("items"))
    add(context.get("catalog"))

    for parent_key in ["shop", "store", "data", "context", "lapakin_context"]:
        parent = context.get(parent_key)
        if isinstance(parent, dict):
            add(parent.get("products"))
            add(parent.get("items"))
            add(parent.get("catalog"))

    products = []
    seen = set()

    for raw in candidates:
        if not isinstance(raw, dict):
            continue

        name = raw.get("name") or raw.get("product_name") or raw.get("title")
        if not name:
            continue

        product_id = raw.get("id") or raw.get("product_id") or raw.get("sku") or name
        key = str(product_id)

        if key in seen:
            continue

        seen.add(key)

        product = dict(raw)
        product["id"] = product_id
        product["name"] = name
        product["price"] = raw.get("price") or raw.get("price_amount") or raw.get("selling_price")
        product["category"] = raw.get("category") or raw.get("category_name")
        products.append(product)

    return products



CUSTOM_REQUEST_TERMS = {
    "custom", "desain sendiri", "design sendiri", "buat desain", "buatkan",
    "bikin", "request", "pesan custom", "custom 3d print", "model sendiri",
    "file sendiri", "estimasi custom", "keychain custom", "gantungan custom",
}


def _is_custom_request_without_specific_product(message: str, products: List[Dict[str, Any]]) -> bool:
    msg_norm = _normalize(message)
    if not any(term in msg_norm for term in CUSTOM_REQUEST_TERMS):
        return False

    # If customer mentions a specific product name or strong owner alias,
    # allow product matcher. Example: "custom warna Oreo Clicker".
    for product in products:
        if not _is_product_active(product):
            continue

        strong_terms = []
        strong_terms.extend(_split_match_text(product.get("name")))
        strong_terms.extend(_split_match_text(product.get("name_en")))
        strong_terms.extend(_split_match_text(product.get("sku")))
        strong_terms.extend(_split_match_text(product.get("bot_aliases")))

        for term in strong_terms:
            term_norm = _normalize(term)
            if not term_norm:
                continue

            # Ignore generic aliases accidentally saved by owner.
            if term_norm in GENERIC_PRODUCT_MATCH_TERMS:
                continue

            # Require a meaningful specific term.
            if len(term_norm) >= 4 and term_norm in msg_norm:
                return False

    return True


def _match_product(message: str, products: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    if _is_custom_request_without_specific_product(message, products):
        return None, 0.0

    msg_norm = _normalize(message)
    msg_tokens = _tokens(msg_norm)

    best = None
    best_score = 0.0

    for product in products:
        if not _is_product_active(product):
            continue

        candidate_terms = _product_match_terms(product)
        if not candidate_terms:
            continue

        score = 0.0

        for term, weight in candidate_terms:
            term_norm = _normalize(term)
            term_tokens = _tokens(term_norm)

            if not term_tokens:
                continue

            term_score = 0.0

            if term_norm and term_norm in msg_norm:
                term_score = weight
            else:
                overlap = len(term_tokens & msg_tokens)
                term_score = (overlap / max(len(term_tokens), 1)) * weight

                # Boost partial long-token match, e.g. "urat" for "Bakso Urat",
                # "keychain" for alias/keyword, or "lampu" for category/search keyword.
                if any(t in msg_tokens for t in term_tokens if len(t) >= 4):
                    term_score = max(term_score, min(weight, 0.55))

            score = max(score, term_score)

        if score > best_score:
            best = product
            best_score = score

    if best_score >= 0.45:
        return best, best_score

    return None, best_score



GENERIC_PRODUCT_MATCH_TERMS = {
    "produk", "product", "spacecraft", "space craft",
    "tanya", "cek", "harga", "price",
    "custom", "custom 3d print", "3d print", "print", "cetak",
    "hadiah", "gift", "kado",
    "gantungan", "keychain",
    "anime", "figur", "figure",
    "lampu", "table lamp", "lamp",
    "order", "pesan", "beli",
    "estimasi", "request", "ukuran", "warna",
}


def _product_term_weight(text: str, weight: float) -> float:
    key = _normalize(text)
    # Generic single/category terms must not be strong enough to select
    # a specific product by themselves. Exact product names stay strong.
    if key in GENERIC_PRODUCT_MATCH_TERMS and weight < 1.0:
        return min(weight, 0.35)
    return weight


def _split_match_text(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;|\n]+", str(value))

    out = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _product_match_terms(product: Dict[str, Any]) -> List[Tuple[str, float]]:
    terms: List[Tuple[str, float]] = []

    def add(value: Any, weight: float):
        for text in _split_match_text(value):
            terms.append((text, weight))

    add(product.get("name"), 1.0)
    add(product.get("name_en"), 0.9)
    add(product.get("slug"), 0.85)
    add(product.get("sku"), 0.85)

    # Owner-curated intelligence has high priority.
    add(product.get("bot_aliases"), 0.95)
    add(product.get("bot_keywords"), 0.78)

    # SpaceCraft feed keywords and product metadata.
    add(product.get("search_keywords"), 0.72)
    add(product.get("category_name") or product.get("category"), 0.62)
    add(product.get("product_type"), 0.55)

    # Short descriptions help generic product phrases, but keep lower weight
    # to avoid over-matching broad words.
    add(product.get("short_description"), 0.50)

    # Deduplicate normalized terms.
    deduped: List[Tuple[str, float]] = []
    seen = set()
    for text, weight in terms:
        key = _normalize(text)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((text, _product_term_weight(text, weight)))

    return deduped


def _get_remembered_product(session_doc: Dict[str, Any], products: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    current = session_doc.get("current_product")
    if not isinstance(current, dict):
        return None

    current_id = current.get("product_id")
    current_name = current.get("name")

    for product in products:
        if current_id and str(product.get("id")) == str(current_id):
            return product
        if current_name and _normalize(product.get("name", "")) == _normalize(current_name):
            return product

    if current_name:
        remembered = dict(current)
        remembered["id"] = current_id
        return remembered

    return None


def _extract_quantity(msg_norm: str) -> Optional[int]:
    digit = re.search(r"\b(\d{1,3})\b", msg_norm)
    if digit:
        value = int(digit.group(1))
        if 0 < value <= 999:
            return value

    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", msg_norm):
            return value

    return None


def _looks_like_followup_price_or_order(msg_norm: str, quantity: Optional[int]) -> bool:
    followup_words = ["kalau", "kalo", "jadi", "total", "berapa", "pesan", "ambil", "mau", "itu", "tadi"]
    return bool(quantity and any(w in msg_norm for w in followup_words))


def _is_product_list_question(msg_norm: str) -> bool:
    patterns = [
        "produk apa",
        "menu apa",
        "apa aja",
        "apa saja",
        "daftar produk",
        "daftar menu",
        "jualan apa",
        "tersedia apa",
    ]
    return any(p in msg_norm for p in patterns)


def _is_price_question(msg_norm: str) -> bool:
    return any(w in msg_norm for w in ["harga", "berapa", "total", "jadi berapa"])


def _is_stock_question(msg_norm: str) -> bool:
    return any(w in msg_norm for w in ["ada", "stok", "ready", "tersedia", "habis"])


def _is_order_intent(msg_norm: str) -> bool:
    return any(w in msg_norm for w in ["pesan", "order", "beli", "mau", "ambil"])


def _is_payment_question(msg_norm: str) -> bool:
    return any(w in msg_norm for w in ["bayar", "qris", "transfer", "rekening", "bca", "bri", "mandiri", "dana", "gopay"])


def _is_hours_question(msg_norm: str) -> bool:
    return any(w in msg_norm for w in ["jam buka", "buka jam", "tutup jam", "hari apa", "operasional"])


def _is_location_question(msg_norm: str) -> bool:
    return any(w in msg_norm for w in ["lokasi", "alamat", "dimana", "di mana", "maps"])


def _extract_shop_name(context: Dict[str, Any]) -> str:
    for path in [
        ("shop", "name"),
        ("store", "name"),
        ("name",),
        ("shop_name",),
    ]:
        value = _get_path(context, path)
        if value:
            return str(value)
    return ""


def _extract_payment_text(context: Dict[str, Any]) -> str:
    for path in [
        ("payment_instruction",),
        ("payment_info", "instruction"),
        ("payment", "instruction"),
        ("storefront_settings", "payment_instruction"),
        ("settings", "payment_instruction"),
        ("shop", "payment_instruction"),
    ]:
        value = _get_path(context, path)
        if value:
            return str(value)
    return ""


def _extract_hours_text(context: Dict[str, Any]) -> str:
    for path in [
        ("business_hours",),
        ("hours",),
        ("shop", "hours"),
        ("shop", "business_hours"),
        ("bot_profile", "business_hours"),
        ("storefront_settings", "business_hours"),
        ("settings", "business_hours"),
    ]:
        value = _get_path(context, path)
        if value:
            return _stringify_simple(value)
    return ""


def _extract_address_text(context: Dict[str, Any]) -> str:
    for path in [
        ("address",),
        ("location",),
        ("shop", "address"),
        ("shop", "location"),
        ("store", "address"),
        ("bot_profile", "address"),
    ]:
        value = _get_path(context, path)
        if value:
            return _stringify_simple(value)
    return ""


def _get_path(data: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _stringify_simple(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if v:
                parts.append(f"{k}: {v}")
        return "; ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value)


def _is_product_active(product: Dict[str, Any]) -> bool:
    if product.get("is_active") is False:
        return False

    status = str(product.get("status") or product.get("visibility") or "").lower()
    if status in {"inactive", "deleted", "draft", "hidden", "archived"}:
        return False

    return True


def _stock_status(product: Dict[str, Any]) -> str:
    if product.get("is_available") is False:
        return "out"

    status = str(product.get("stock_status") or product.get("availability") or "").lower()
    if status in {"out", "out_of_stock", "sold_out", "habis", "kosong"}:
        return "out"

    return "unknown"


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None

    text = str(value)
    digits = re.sub(r"[^0-9]", "", text)

    if not digits:
        return None

    number = float(digits)
    return number if number > 0 else None


def _format_rupiah(value: Any) -> str:
    number = _to_number(value) or 0
    return "Rp" + f"{int(number):,}".replace(",", ".")


def _normalize(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _tokens(value: str) -> set:
    return {t for t in _normalize(value).split() if t and t not in STOPWORDS}

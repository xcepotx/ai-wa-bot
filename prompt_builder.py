"""
Prompt Builder — build system prompt dari context toko Lapakin.
RAG sederhana: inject data toko, produk, FAQ ke system prompt.
"""
from datetime import datetime, timezone, timedelta


TONE_GUIDE = {
    "ramah":       "Gunakan bahasa yang hangat, sopan, dan penuh empati. Sapa dengan 'Kak'.",
    "santai":      "Gunakan bahasa santai dan friendly. Boleh pakai emoji secukupnya.",
    "profesional": "Gunakan bahasa formal dan profesional. Hindari singkatan tidak baku.",
    "singkat":     "Jawab singkat dan to the point. Maksimal 2-3 kalimat per jawaban.",
    "ceria":       "Gunakan bahasa ceria dan antusias. Boleh pakai emoji yang cukup.",
}


def build_system_prompt(context: dict) -> str:
    shop         = context.get("shop", {})
    payment      = context.get("payment", {})
    products     = context.get("products", [])
    faqs         = context.get("faqs", [])
    bot_settings = context.get("bot_settings", {})

    shop_name  = shop.get("name", "Toko ini")
    bot_name   = bot_settings.get("bot_name", "Admin")
    tone       = bot_settings.get("tone", "ramah")
    tone_guide = TONE_GUIDE.get(tone, TONE_GUIDE["ramah"])
    fallback   = bot_settings.get("fallback_message", "Maaf kak, silakan hubungi admin kami ya 🙏")
    handoff_kw = bot_settings.get("handoff_keywords", [])

    # Jam operasional
    hours_text = shop.get("hours") or _build_hours_from_schedule(shop.get("schedule", []))

    # Produk — max 30 item agar prompt tidak terlalu panjang
    product_lines = []
    for p in products[:30]:
        status = ""
        if p.get("availability_status") == "out_of_stock" or p.get("stock", 1) == 0:
            status = " [HABIS]"
        raw_price = p.get("price")
        price_label = (p.get("price_label") or "").strip()

        if raw_price is None:
            price_text = price_label or "Harga perlu dikonfirmasi admin"
        else:
            try:
                price_value = float(raw_price)
                if price_value > 0:
                    price_text = f"Rp {int(price_value):,}".replace(",", ".")
                else:
                    price_text = price_label or "Harga perlu dikonfirmasi admin"
            except (TypeError, ValueError):
                price_text = price_label or "Harga perlu dikonfirmasi admin"

        line = f"- {p.get('name', 'Produk')}: {price_text}"
        if p.get("description"):
            line += f" — {p['description'][:80]}"
        line += status
        product_lines.append(line)
    products_text = "\n".join(product_lines) if product_lines else "Belum ada produk."

    # Payment
    payment_parts = []
    if payment.get("instruction"):
        payment_parts.append(payment["instruction"])
    if payment.get("qris_image"):
        payment_parts.append("QRIS tersedia.")
    for bank in (payment.get("bank_accounts") or []):
        payment_parts.append(
            f"Transfer {bank.get('bank','')} {bank.get('number','')} a.n. {bank.get('name','')}"
        )
    if payment.get("payment_notes"):
        payment_parts.append(payment["payment_notes"])
    payment_text = " | ".join(payment_parts) if payment_parts else "Hubungi admin untuk info pembayaran."

    # FAQ
    faq_lines = []
    for faq in faqs[:20]:
        faq_lines.append(f"T: {faq['question']}\nJ: {faq['answer']}")
    faq_text = "\n\n".join(faq_lines) if faq_lines else ""

    # Info tambahan
    extras = []
    if shop.get("order_methods"):
        extras.append(f"Metode order: {', '.join(shop['order_methods'])}")
    if shop.get("service_area"):
        extras.append(f"Area layanan: {shop['service_area']}")
    if shop.get("min_order"):
        extras.append(f"Minimum order: Rp {int(shop['min_order']):,}".replace(",", "."))
    if shop.get("preorder_policy"):
        extras.append(f"Preorder: {shop['preorder_policy']}")
    if shop.get("store_notes"):
        extras.append(f"Catatan: {shop['store_notes']}")
    extras_text = "\n".join(extras)

    # Promo
    promo_text = ""
    if shop.get("promo_active") and shop.get("promo_title"):
        promo_text = f"\nPROMO AKTIF: {shop['promo_title']}"
        if shop.get("promo_description"):
            promo_text += f" — {shop['promo_description']}"

    # Handoff keywords
    handoff_text = ""
    if handoff_kw:
        handoff_text = f"\nJika pelanggan menyebut kata-kata: {', '.join(handoff_kw)} — segera arahkan ke admin manusia."

    prompt = f"""Kamu adalah asisten WhatsApp untuk {shop_name}. Namamu adalah {bot_name}.

GAYA BAHASA:
{tone_guide}

IDENTITAS TOKO:
Nama: {shop_name}
{f"Deskripsi: {shop.get('description')}" if shop.get('description') else ""}
{f"Alamat: {shop.get('address')}" if shop.get('address') else ""}
{f"Jam buka: {hours_text}" if hours_text else ""}
{extras_text}
{promo_text}

DAFTAR PRODUK:
{products_text}

INFORMASI PEMBAYARAN:
{payment_text}
"""

    if faq_text:
        prompt += f"""
FAQ TOKO (gunakan ini untuk menjawab pertanyaan umum):
{faq_text}
"""

    prompt += f"""
ATURAN WAJIB:
1. JANGAN mengarang harga, stok, atau informasi yang tidak ada di atas.
2. Jika tidak tahu jawabannya, katakan jujur dan arahkan ke admin.
3. Jawab hanya seputar toko ini. Tolak topik di luar konteks toko.
4. Jangan berpura-pura jadi manusia jika ditanya langsung.
5. Jawaban maksimal 3-4 kalimat, kecuali diminta lebih detail.
{handoff_text}

FALLBACK (jika tidak bisa menjawab):
{fallback}
"""

    return prompt.strip()


def build_handoff_check_prompt(message: str, handoff_keywords: list) -> str:
    """Prompt cepat untuk deteksi apakah pesan butuh handoff ke manusia."""
    kw = ", ".join(handoff_keywords) if handoff_keywords else "komplain, refund, batal"
    return f"""Apakah pesan berikut mengandung permintaan untuk berbicara dengan manusia/admin, 
atau menyebut kata-kata sensitif seperti: {kw}?

Pesan: "{message}"

Jawab hanya dengan: YES atau NO"""


def _build_hours_from_schedule(schedule: list) -> str:
    """Convert schedule array ke string yang mudah dibaca."""
    if not schedule:
        return ""
    days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    parts = []
    for i, day in enumerate(schedule[:7]):
        if not day:
            continue
        shifts = day.get("shifts") or ([day] if day.get("open") else [])
        if not shifts:
            continue
        times = ", ".join(f"{s.get('open','?')}-{s.get('close','?')}" for s in shifts)
        parts.append(f"{days[i]}: {times}")
    return " | ".join(parts) if parts else ""

#!/bin/bash
# Smoke test ai-wa-bot simulator
# Jalankan: SHOP_ID=shop_xxx bash test_simulate.sh

BOT_URL=${BOT_URL:-"http://127.0.0.1:8002"}
SHOP_ID=${SHOP_ID:-"shop_290a508e7c59"}

echo "=== AI WA Bot Simulator Test ==="
echo "URL: $BOT_URL"
echo "Shop: $SHOP_ID"
echo ""

PASS="\033[92m✓\033[0m"
FAIL="\033[91m✗\033[0m"

# 1. Health check
echo "[ 1 ] Health check"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" $BOT_URL/health)
[ "$STATUS" = "200" ] && echo -e "  $PASS Health OK" || echo -e "  $FAIL Health FAIL ($STATUS)"

# 2. Test tanya produk
echo ""
echo "[ 2 ] Tanya menu/produk"
RESP=$(curl -s -X POST $BOT_URL/api/simulate \
  -H "Content-Type: application/json" \
  -d "{\"shop_id\":\"$SHOP_ID\",\"customer_message\":\"menu apa aja kak?\"}")
REPLY=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bot_reply','ERROR')[:100])" 2>/dev/null)
INTENT=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('intent','?'))" 2>/dev/null)
MS=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response_ms','?'))" 2>/dev/null)
[ -n "$REPLY" ] && echo -e "  $PASS Reply: $REPLY..." || echo -e "  $FAIL No reply"
echo "     Intent: $INTENT | ${MS}ms"

# 3. Test tanya harga
echo ""
echo "[ 3 ] Tanya harga spesifik"
RESP=$(curl -s -X POST $BOT_URL/api/simulate \
  -H "Content-Type: application/json" \
  -d "{\"shop_id\":\"$SHOP_ID\",\"customer_message\":\"berapa harga bakso urat?\"}")
REPLY=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bot_reply','ERROR')[:100])" 2>/dev/null)
SOURCE=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('source','?'))" 2>/dev/null)
[ -n "$REPLY" ] && echo -e "  $PASS Reply: $REPLY..." || echo -e "  $FAIL No reply"
echo "     Source: $SOURCE"

# 4. Test tanya payment
echo ""
echo "[ 4 ] Tanya pembayaran"
RESP=$(curl -s -X POST $BOT_URL/api/simulate \
  -H "Content-Type: application/json" \
  -d "{\"shop_id\":\"$SHOP_ID\",\"customer_message\":\"bisa bayar pakai QRIS?\"}")
REPLY=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bot_reply','ERROR')[:100])" 2>/dev/null)
[ -n "$REPLY" ] && echo -e "  $PASS Reply: $REPLY..." || echo -e "  $FAIL No reply"

# 5. Test handoff keyword
echo ""
echo "[ 5 ] Handoff keyword (komplain)"
RESP=$(curl -s -X POST $BOT_URL/api/simulate \
  -H "Content-Type: application/json" \
  -d "{\"shop_id\":\"$SHOP_ID\",\"customer_message\":\"saya mau komplain pesanan kemarin salah\"}")
HANDOFF=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('handoff_required','?'))" 2>/dev/null)
[ "$HANDOFF" = "True" ] && echo -e "  $PASS Handoff terdeteksi dengan benar" || echo -e "  $FAIL Handoff tidak terdeteksi (got: $HANDOFF)"

# 6. Test pertanyaan di luar konteks
echo ""
echo "[ 6 ] Pertanyaan di luar konteks"
RESP=$(curl -s -X POST $BOT_URL/api/simulate \
  -H "Content-Type: application/json" \
  -d "{\"shop_id\":\"$SHOP_ID\",\"customer_message\":\"berapa harga iPhone 15?\"}")
REPLY=$(echo $RESP | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bot_reply','ERROR')[:100])" 2>/dev/null)
[ -n "$REPLY" ] && echo -e "  $PASS Reply: $REPLY..." || echo -e "  $FAIL No reply"

echo ""
echo "=== Test selesai ==="

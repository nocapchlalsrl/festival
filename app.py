import os
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, g, redirect
from werkzeug.utils import secure_filename
from functools import wraps

API_BASE = "/api/v1"
MASTER_KEY = os.environ.get("MASTER_KEY", "chlalsrlWKd")

UPLOAD_DIR = "uploads"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__, static_folder=".")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ===== in-memory DB =====
# Booth: {id, name, description, capacity, imageUrl, adminKey, isOpen, closedReason}
BOOTH_LIST = []
# Menu: {id, boothId, name, price, imageUrl, maxQty, options}
#   - maxQty: int (0이면 무제한)
#   - options: list[ {code,label,priceDelta} ]  (없으면 [])
MENU_LIST = []
# Reservation: {id(int), boothId, studentNo, studentName, phone, items:[{menuId,qty}], total, status, createdAt, doneAt, cancelledAt}
RESERVATION_LIST = []

# ===== utils =====
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def to_int_id(x):
    if isinstance(x, int):
        return x
    if isinstance(x, str) and x.isdigit():
        return int(x)
    return x

def get_booth_by_id(booth_id):
    return next((b for b in BOOTH_LIST if b["id"] == booth_id), None)

def get_menu_by_id(menu_id):
    return next((m for m in MENU_LIST if m["id"] == menu_id), None)

def get_reservation_by_id(resv_id):
    return next((r for r in RESERVATION_LIST if r.get("id") == resv_id), None)

def next_resv_id():
    if not RESERVATION_LIST:
        return 1
    ids = [r["id"] for r in RESERVATION_LIST if isinstance(r.get("id"), int)]
    return (max(ids) + 1) if ids else 1

def normalize_menu_obj(m: dict) -> dict:
    """기존 데이터 호환: maxQty/options 누락 시 기본값 보정"""
    mm = dict(m)
    try:
        mm["maxQty"] = int(mm.get("maxQty") or 0)
    except:
        mm["maxQty"] = 0
    opts = mm.get("options")
    if opts is None or not isinstance(opts, list):
        mm["options"] = []
    return mm

def calc_total_and_normalize_items(booth_id, items):
    booth_menus = {m["id"]: normalize_menu_obj(m) for m in MENU_LIST if m["boothId"] == booth_id}
    total = 0
    norm_items = []
    for it in items:
        mid = (it.get("menuId") or "").strip()
        qty = int(it.get("qty") or 0)
        if qty <= 0:
            continue

        menu = booth_menus.get(mid)
        if not menu:
            raise ValueError(f"존재하지 않거나 다른 부스 메뉴: {mid}")

        # ✅ maxQty 제한 (0이면 무제한)
        max_qty = int(menu.get("maxQty") or 0)
        if max_qty > 0 and qty > max_qty:
            raise ValueError(f"{menu.get('name') or mid} 최대 {max_qty}개까지 구매 가능합니다.")

        total += int(menu.get("price") or 0) * qty
        norm_items.append({"menuId": mid, "qty": qty})

    if not norm_items:
        raise ValueError("유효한 주문 항목이 없습니다.")
    return total, norm_items

# ===== auth =====
def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-Admin-Key") or request.args.get("key")
        if not key:
            return jsonify({"error": "Unauthorized: 관리자 키 필요"}), 401

        g.role = None          # MASTER / BOOTH
        g.allowed_booth_id = None

        if key == MASTER_KEY:
            g.role = "MASTER"
            return f(*args, **kwargs)

        booth = next((b for b in BOOTH_LIST if b.get("adminKey") == key), None)
        if booth:
            g.role = "BOOTH"
            g.allowed_booth_id = booth["id"]
            return f(*args, **kwargs)

        return jsonify({"error": "Unauthorized: 잘못된 관리자 키"}), 401
    return decorated

# ===== static pages =====
@app.get("/")
def home():
    return redirect("/index")

@app.get("/index")
def serve_index():
    return send_from_directory(".", "index.html")

@app.get("/admin")
def serve_admin():
    return send_from_directory(".", "admin.html")

@app.get("/js/<path:filename>")
def serve_js(filename):
    return send_from_directory("js", filename)

@app.get("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ===== Public APIs =====
@app.get(f"{API_BASE}/booths")
def api_booths():
    pubs = [{k: v for k, v in b.items() if k != "adminKey"} for b in BOOTH_LIST]
    return jsonify(pubs)

@app.get(f"{API_BASE}/booths/<booth_id>")
def api_booth(booth_id):
    b = get_booth_by_id(booth_id)
    if not b:
        return jsonify({"error": "Booth not found"}), 404
    pub = {k: v for k, v in b.items() if k != "adminKey"}
    return jsonify(pub)

@app.get(f"{API_BASE}/booths/<booth_id>/menus")
def api_booth_menus(booth_id):
    if not get_booth_by_id(booth_id):
        return jsonify({"error": "Booth not found"}), 404

    raw = [m for m in MENU_LIST if m["boothId"] == booth_id]
    menus = [normalize_menu_obj(m) for m in raw]  # ✅ maxQty/options 포함 보정
    return jsonify(menus)

@app.post(f"{API_BASE}/orders")
def api_create_order():
    data = request.get_json(silent=True) or {}

    booth_id = (data.get("boothId") or "").strip()
    student_no = (data.get("studentNo") or "").strip()
    student_name = (data.get("studentName") or "").strip()
    phone = (data.get("phone") or "").strip()
    items = data.get("items") or []

    if not booth_id or not student_no or not student_name or not phone:
        return jsonify({"error": "boothId, studentNo, studentName, phone 필수"}), 400
    if len(student_no) != 4 or not student_no.isdigit():
        return jsonify({"error": "학번은 숫자 4자리"}), 400
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items 필요"}), 400

    booth = get_booth_by_id(booth_id)
    if not booth:
        return jsonify({"error": "존재하지 않는 부스"}), 404

    # ✅ 대기열 중지면 주문 막기
    if not booth.get("isOpen", True):
        reason = booth.get("closedReason") or "현재 대기열이 잠시 중지되었습니다."
        return jsonify({"error": reason, "code": "BOOTH_CLOSED"}), 409

    try:
        total, norm_items = calc_total_and_normalize_items(booth_id, items)  # ✅ maxQty 검증 포함
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    rec = {
        "id": next_resv_id(),  # int
        "boothId": booth_id,
        "studentNo": student_no,
        "studentName": student_name,
        "phone": phone,
        "items": norm_items,
        "total": total,
        "status": "CONFIRMED",
        "createdAt": now_iso(),
        "doneAt": None,
        "cancelledAt": None
    }
    RESERVATION_LIST.append(rec)
    return jsonify({"ok": True, "id": rec["id"], "total": total}), 201

# ===== Admin APIs =====
@app.get(f"{API_BASE}/admin/whoami")
@require_admin
def admin_whoami():
    return jsonify({"role": g.role, "allowedBoothId": g.allowed_booth_id})

@app.post(f"{API_BASE}/admin/upload")
@require_admin
def admin_upload():
    if "file" not in request.files:
        return jsonify({"error": "file 필드 필요"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "파일명 없음"}), 400
    if not allowed_file(f.filename):
        return jsonify({"error": "허용 확장자: png,jpg,jpeg,webp,gif"}), 400

    base = secure_filename(f.filename)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    name = f"{ts}_{base}"
    f.save(os.path.join(UPLOAD_DIR, name))
    return jsonify({"ok": True, "url": f"/uploads/{name}"}), 201

@app.post(f"{API_BASE}/admin/booths")
@require_admin
def admin_create_booth():
    if g.role != "MASTER":
        return jsonify({"error": "Master key required"}), 403

    data = request.get_json(silent=True) or {}
    bid = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    admin_key = (data.get("adminKey") or "").strip()
    desc = (data.get("description") or "").strip()
    cap = int(data.get("capacity") or 0)
    img = (data.get("imageUrl") or "").strip()

    if not bid or not name or not admin_key:
        return jsonify({"error": "부스ID, 이름, 부스키(adminKey) 필수"}), 400
    if get_booth_by_id(bid):
        return jsonify({"error": f"이미 존재하는 부스 id: {bid}"}), 409
    if any(b.get("adminKey") == admin_key for b in BOOTH_LIST):
        return jsonify({"error": "이미 사용중인 부스키(adminKey)입니다."}), 409

    BOOTH_LIST.append({
        "id": bid,
        "name": name,
        "description": desc,
        "capacity": cap,
        "imageUrl": img,
        "adminKey": admin_key,
        "isOpen": True,
        "closedReason": ""
    })
    return jsonify({"ok": True, "id": bid}), 201

# ✅ 부스 대기열 중지/재개
@app.post(f"{API_BASE}/admin/booths/status")
@require_admin
def admin_set_booth_status():
    data = request.get_json(silent=True) or {}
    booth_id = (data.get("boothId") or "").strip()
    is_open = data.get("isOpen")
    reason = (data.get("reason") or "").strip()

    if booth_id == "" or not isinstance(is_open, bool):
        return jsonify({"error": "boothId, isOpen(boolean) 필수"}), 400

    b = get_booth_by_id(booth_id)
    if not b:
        return jsonify({"error": "부스 없음"}), 404

    if g.role == "BOOTH" and booth_id != g.allowed_booth_id:
        return jsonify({"error": "Forbidden"}), 403

    b["isOpen"] = is_open
    b["closedReason"] = "" if is_open else (reason or "현재 주문이 많아 잠시 중지합니다.")
    return jsonify({"ok": True, "boothId": booth_id, "isOpen": b["isOpen"], "reason": b["closedReason"]})

@app.post(f"{API_BASE}/admin/menus")
@require_admin
def admin_create_menu():
    data = request.get_json(silent=True) or {}
    mid = (data.get("id") or "").strip()
    booth_id = (data.get("boothId") or "").strip()
    name = (data.get("name") or "").strip()
    price = int(data.get("price") or 0)
    img = (data.get("imageUrl") or "").strip()

    # ✅ 추가 필드 (admin에서 보내는 maxQty/options 받기)
    try:
        max_qty = int(data.get("maxQty") or 0)  # 0=무제한
    except:
        return jsonify({"error": "maxQty는 숫자여야 합니다."}), 400

    options = data.get("options")
    if options is None:
        options = []
    if not isinstance(options, list):
        return jsonify({"error": "options는 배열(JSON Array)이어야 합니다."}), 400

    if not mid or not booth_id or not name:
        return jsonify({"error": "id, boothId, name 필수"}), 400
    if price < 0:
        return jsonify({"error": "가격은 0 이상"}), 400
    if max_qty < 0:
        return jsonify({"error": "maxQty는 0 이상"}), 400
    if not get_booth_by_id(booth_id):
        return jsonify({"error": "부스 없음"}), 404

    if g.role == "BOOTH" and booth_id != g.allowed_booth_id:
        return jsonify({"error": "Forbidden: 다른 부스 메뉴 등록 불가"}), 403

    if get_menu_by_id(mid):
        return jsonify({"error": f"이미 존재하는 메뉴 id: {mid}"}), 409

    MENU_LIST.append({
        "id": mid,
        "boothId": booth_id,
        "name": name,
        "price": price,
        "imageUrl": img,
        "maxQty": max_qty,   # ✅ 저장
        "options": options   # ✅ 저장
    })
    return jsonify({"ok": True, "id": mid}), 201

@app.get(f"{API_BASE}/admin/menus")
@require_admin
def admin_list_menus():
    booth_id = (request.args.get("boothId") or "").strip()
    if not booth_id:
        return jsonify({"error": "boothId가 필요합니다."}), 400

    if g.role == "BOOTH" and booth_id != g.allowed_booth_id:
        return jsonify({"error": "Forbidden: 다른 부스 메뉴 조회 불가"}), 403

    raw = [m for m in MENU_LIST if m["boothId"] == booth_id]
    items = [normalize_menu_obj(m) for m in raw]  # ✅ maxQty/options 포함 보정
    return jsonify({"items": items})

@app.get(f"{API_BASE}/admin/reservations")
@require_admin
def admin_list_reservations():
    booth_id = (request.args.get("boothId") or "").strip()
    if not booth_id:
        return jsonify({"error": "boothId가 필요합니다."}), 400

    if g.role == "BOOTH" and booth_id != g.allowed_booth_id:
        return jsonify({"error": "Forbidden: 다른 부스 예약 조회 불가"}), 403

    rows = [r for r in RESERVATION_LIST if r["boothId"] == booth_id]
    rows = sorted(rows, key=lambda r: r["createdAt"], reverse=True)
    return jsonify({"items": rows})

# ✅ 예약 상태 변경(처리/취소)
@app.post(f"{API_BASE}/admin/reservations/status")
@require_admin
def admin_set_reservation_status():
    data = request.get_json(silent=True) or {}
    rid = to_int_id(data.get("id"))
    status = (data.get("status") or "").strip().upper()

    if rid is None or not status:
        return jsonify({"error": "id, status 필수"}), 400
    if status not in ("CONFIRMED", "DONE", "CANCELLED"):
        return jsonify({"error": "status는 CONFIRMED/DONE/CANCELLED 중 하나"}), 400

    r = get_reservation_by_id(rid)
    if not r:
        return jsonify({"error": "예약 없음"}), 404

    if g.role == "BOOTH" and r.get("boothId") != g.allowed_booth_id:
        return jsonify({"error": "Forbidden: 다른 부스 예약 변경 불가"}), 403

    r["status"] = status
    if status == "DONE":
        r["doneAt"] = now_iso()
    if status == "CANCELLED":
        r["cancelledAt"] = now_iso()

    return jsonify({"ok": True, "id": rid, "status": r["status"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

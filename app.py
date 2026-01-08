from flask import Flask, request, jsonify
import os, json, traceback

import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# ------------------------------------
# Flask
# ------------------------------------
app = Flask(__name__)

# ------------------------------------
# Firebase Config
# ------------------------------------
RTD_URL1 = "https://bestofm-a31a0-default-rtdb.asia-southeast1.firebasedatabase.app/"
BUCKET_NAME = "bestofm-a31a0.firebasestorage.app"

service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
if not service_account_json:
    raise RuntimeError("Missing FIREBASE_SERVICE_KEY")

cred = credentials.Certificate(json.loads(service_account_json))

firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME,
        "databaseURL": RTD_URL1
    }
)

# ✅ ใช้ Firebase Admin เท่านั้น (ไม่มี ADC)
db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()

# ------------------------------------
# Utils
# ------------------------------------
def build_prefixes(text: str):
    text = text.lower().strip()
    prefixes = []
    current = ""
    for ch in text:
        current += ch
        prefixes.append(current)
    return prefixes

# ------------------------------------
# Admin Login
# ------------------------------------
@app.route("/ofm_password", methods=["POST"])
def ofm_password():
    try:
        data = request.get_json()
        nameofm = data.get("nameofm")
        adminpassword = data.get("adminpassword")

        if not nameofm or not adminpassword:
            return jsonify({"status": "error", "message": "ข้อมูลไม่ครบ"}), 400

        query = (
            db.collection("registeradminOFM")
            .where("nameofm", "==", nameofm)
            .limit(1)
            .stream()
        )

        admin_doc = next(query, None)
        if not admin_doc:
            return jsonify({"status": "not_found"}), 200

        doc_data = admin_doc.to_dict()
        if not check_password_hash(doc_data.get("addminpass"), adminpassword):
            return jsonify({"status": "wrong_password"}), 200

        return jsonify({
            "status": "success",
            "adminname": doc_data.get("admin_name", ""),
            "adminadd": doc_data.get("adminadd", "")
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------------------
# Register Admin + OFM
# ------------------------------------
@app.route("/register_admin_full", methods=["POST"])
def register_admin_full():
    try:
        data = request.get_json()

        nameofm = data.get("nameofm", "").strip()
        admin_name = data.get("adminname")
        admin_add = data.get("adminadd")
        admin_phone = data.get("adminphone")
        admin_pass = data.get("addminpass")

        if not nameofm or not admin_name or not admin_phone or not admin_pass:
            return jsonify({"status": "error", "message": "ข้อมูลไม่ครบ"}), 400

        # check OFM duplicate
        ofm_ref = db.collection("OFM_name").document(nameofm)
        if ofm_ref.get().exists:
            return jsonify({"status": "error", "message": "ชื่อร้านซ้ำ"}), 200

        if not admin_pass.isdigit() or len(admin_pass) != 6:
            return jsonify({"status": "error", "message": "รหัสผ่านต้อง 6 หลัก"}), 200

        ofm_ref.set({
            "OFM_name": nameofm,
            "OFM_name_lower": nameofm.lower(),
            "search_prefix": build_prefixes(nameofm),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        db.collection("registeradminOFM").add({
            "nameofm": nameofm,
            "admin_name": admin_name,
            "adminadd": admin_add,
            "adminphone": admin_phone,
            "addminpass": generate_password_hash(admin_pass),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        # create storage folder
        blob = bucket.blob(f"{nameofm}/.keep")
        blob.upload_from_string("")

        return jsonify({"status": "success"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------------------
# Search OFM
# ------------------------------------
@app.route("/search_adminmaster", methods=["GET"])
def search_adminmaster():
    keyword = request.args.get("q", "").lower().strip()
    if not keyword:
        return jsonify([])

    docs = (
        db.collection("OFM_name")
        .where("search_prefix", "array_contains", keyword)
        .limit(50)
        .stream()
    )

    return jsonify([{"OFM_name": d.to_dict().get("OFM_name")} for d in docs])

# ------------------------------------
# Hierarchical Storage APIs
# ------------------------------------
@app.route("/get_shops", methods=["GET"])
def get_shops():
    ofm = request.args.get("ofm")
    if not ofm:
        return jsonify({"error": "missing ofm"}), 400

    prefix = f"{ofm}/"
    shops = set()

    for blob in bucket.list_blobs(prefix=prefix):
        parts = blob.name.split("/")
        if len(parts) >= 2:
            shops.add(parts[1])

    return jsonify({"ofm": ofm, "shops": sorted(shops)})

@app.route("/get_modes", methods=["GET"])
def get_modes():
    ofm = request.args.get("ofm")
    shop = request.args.get("shop")

    if not ofm or not shop:
        return jsonify({"error": "missing params"}), 400

    prefix = f"{ofm}/{shop}/"
    modes = set()

    for blob in bucket.list_blobs(prefix=prefix):
        parts = blob.name.split("/")
        if len(parts) >= 3:
            modes.add(parts[2])

    return jsonify({"shop": shop, "modes": sorted(modes)})

@app.route("/get_images", methods=["GET"])
def get_images():
    ofm = request.args.get("ofm")
    shop = request.args.get("shop")
    mode = request.args.get("mode")

    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))

    if not ofm or not shop or not mode:
        return jsonify({"error": "missing params"}), 400

    prefix = f"{ofm}/{shop}/{mode}/"
    images = []

    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.lower().endswith(".jpg"):
            images.append(
                f"https://storage.googleapis.com/{bucket.name}/{blob.name}"
            )

    total = len(images)
    start = (page - 1) * page_size
    end = start + page_size

    return jsonify({
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": end < total,
        "images": images[start:end]
    })
#---------------------------------------
@app.route("/register_slave", methods=["POST"])
def register_slave():
    try:
        data = request.json

        name_ofm = data.get("name_ofm")
        slavename = data.get("slavename")
        address = data.get("address")
        phone = data.get("phone")
        password = data.get("password")

        if not all([name_ofm, slavename, address, phone, password]):
            return jsonify({
                "success": False,
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # ---------------- Firestore Path ----------------
        ofm_ref = db.collection("OFM_name").document(name_ofm)
        slave_ref = (
            ofm_ref
            .collection("partner")
            .document(slavename)
        )

        # ---------------- Check Duplicate ----------------
        if slave_ref.get().exists:
            return jsonify({
                "success": False,
                "message": "ชื่อร้านค้าซ้ำ กรุณาตั้งชื่อใหม่"
            }), 409

        # ---------------- Save Firestore ----------------
        ofm_ref.set({
            "OFM_name": name_ofm,
            "updated_at": datetime.utcnow()
        }, merge=True)

        slave_ref.set({
            "slavename": slavename,
            "address": address,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.utcnow()
        })

        # ---------------- Create Storage Folder ----------------
        bucket = storage.bucket()

        # สร้าง folder หลักของ OFM ถ้ายังไม่มี
        ofm_folder_blob = bucket.blob(f"{name_ofm}/.keep")
        if not ofm_folder_blob.exists():
            ofm_folder_blob.upload_from_string(
                "",
                content_type="text/plain"
            )

        # (optional) สร้าง folder ของร้านค้า slave
        slave_folder_blob = bucket.blob(f"{name_ofm}/{slavename}/.keep")
        if not slave_folder_blob.exists():
            slave_folder_blob.upload_from_string(
                "",
                content_type="text/plain"
            )

        return jsonify({
            "success": True,
            "message": "ลงทะเบียนสำเร็จ"
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500
#------------------------------------
@app.route("/slave_password", methods=["POST"])
def slave_password():
    try:
        data = request.get_json()

        name_ofm = data.get("name_ofm", "").strip()
        slave_name = data.get("slave_name", "").strip()
        slave_password = data.get("slave_password", "").strip()

        # 🔒 validate input
        if not name_ofm or not slave_name or not slave_password:
            return jsonify({
                "status": "error",
                "message": "missing_parameters"
            }), 400

        # 📌 path:
        # OFM_name/{name_ofm}/partner/{slave_name}
        slave_ref = (
            db.collection("OFM_name")
              .document(name_ofm)
              .collection("partner")
              .document(slave_name)
        )

        doc = slave_ref.get()

        # ❌ ไม่พบร้าน
        if not doc.exists:
            return jsonify({
                "status": "not_found"
            }), 200

        slave_data = doc.to_dict()
        saved_password = slave_data.get("password")

        # ❌ รหัสผ่านไม่ถูก
        if saved_password != slave_password:
            return jsonify({
                "status": "wrong_password"
            }), 200

        # ✅ ผ่าน
        return jsonify({
            "status": "success",
            "nameofm": name_ofm,
            "slavename": slave_name
        }), 200

    except Exception as e:
        print("SLAVE PASSWORD ERROR:", str(e))
        return jsonify({
            "status": "server_error",
            "message": str(e)
        }), 500


# ------------------------------------
if __name__ == "__main__":
    app.run(debug=True)

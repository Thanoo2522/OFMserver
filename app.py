from flask import Flask, request, jsonify, send_file
import os, json, base64, traceback, tempfile
from io import BytesIO

import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore

from openai import OpenAI
from PIL import Image
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

 
 
from google.cloud import storage
#-------------------------------------
import qrcode
import io
import uuid
import time
 
INSTALL_URL = "https://jai.app/install"

# ------------------- Flask ----------- 
app = Flask(__name__)

# ------------------- Firebase Config -------------------
RTD_URL1 = "https://bestofm-a31a0-default-rtdb.asia-southeast1.firebasedatabase.app/" # realtime database
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

storage_client = storage.Client()
db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()
#-----------------------------------------------------------
def build_prefixes(text):
    text = text.lower().strip()
    prefixes = []
    current = ""
    for ch in text:
        current += ch
        prefixes.append(current)
    return prefixes
# ------------ข้อมูลการทงทะเบียนผู้ดูแลระบบ สร้างตลาด fresh market
 
#----------------- check password เพื่อเข้าหน้า singmasterpage  ----
@app.route("/ofm_password", methods=["POST"])
def ofm_password():
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON received"
            }), 400

        nameofm = data.get("nameofm")
        adminpassword = data.get("adminpassword")

        if not nameofm or not adminpassword:
            return jsonify({
                "status": "error",
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # 🔹 ค้น admin จาก nameofm
        query = (
            db.collection("registeradminOFM")
            .where("nameofm", "==", nameofm)
            .limit(1)
            .stream()
        )

        admin_doc = None
        for doc in query:
            admin_doc = doc
            break

        if not admin_doc:
            return jsonify({
                "status": "not_found"
            }), 200

        doc_data = admin_doc.to_dict()
        saved_hashed_password = doc_data.get("addminpass")

        # 🔐 ตรวจรหัสผ่าน
        if not check_password_hash(saved_hashed_password, adminpassword):
            return jsonify({
                "status": "wrong_password"
            }), 200

        # ✅ สำเร็จ
        return jsonify({
            "status": "success",
            "adminname": doc_data.get("admin_name", ""),
            "adminadd": doc_data.get("adminadd", "")
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

 #-------------------บันทึกชื่อ ofmname จากการตั้งชื่อ ตลากสดอออนไลด์ -----------------------
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
            return jsonify({
                "status": "error",
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # ---------- 1️⃣ เช็คชื่อ OFM ซ้ำ (สำคัญ) ----------
        ofm_ref = db.collection("OFM_name").document(nameofm)
        if ofm_ref.get().exists:
            return jsonify({
                "status": "error",
                "message": "ชื่อร้านซ้ำ"
            }), 200

        # ---------- 2️⃣ ตรวจรหัสผ่าน ----------
        if not admin_pass.isdigit() or len(admin_pass) != 6:
            return jsonify({
                "status": "error",
                "message": "รหัสผ่านต้องเป็นตัวเลข 6 หลัก"
            }), 200

        # ---------- 3️⃣ บันทึก OFM ----------
        ofm_ref.set({
            "OFM_name": nameofm,
            "OFM_name_lower": nameofm.lower(),
            "search_prefix": build_prefixes(nameofm),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        # ---------- 4️⃣ บันทึก Admin (ซ้ำได้) ----------
        hashed_pass = generate_password_hash(admin_pass)

        db.collection("registeradminOFM").add({
            "nameofm": nameofm,
            "admin_name": admin_name,
            "adminadd": admin_add,
            "adminphone": admin_phone,
            "addminpass": hashed_pass,
            "created_at": firestore.SERVER_TIMESTAMP
        })

        # ---------- 5️⃣ สร้าง folder ใน Firebase Storage ----------
        bucket = storage.bucket()
        blob = bucket.blob(f"{nameofm}/.keep")
        blob.upload_from_string("", content_type="text/plain")

        return jsonify({
            "status": "success"
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


 #-------------------ระบบค้าหา adminmastername   ตลากสดอออนไลด์ ------------ 
@app.route("/search_adminmaster", methods=["GET"])
def search_adminmaster():
    keyword = request.args.get("q", "").strip().lower()

    if not keyword:
        return jsonify([])

    results = (
        db.collection("OFM_name")
        .where("search_prefix", "array_contains", keyword)
        .limit(50)
        .stream()
    )

    data = []
    for doc in results:
        d = doc.to_dict()
        data.append({
            "OFM_name": d.get("OFM_name")
        })

    return jsonify(data)
    #---------------------โหลดรายชื่อแผงทั้งหมด ---------------------
@app.route("/get_shops", methods=["GET"])
def get_shops():
    """
    ?ofm=ตลาดสดมารวย
    """
    ofm = request.args.get("ofm")

    if not ofm:
        return jsonify({"error": "missing ofm"}), 400

    prefix = f"{ofm}/"
    shops = set()

    blobs = storage_client.list_blobs(bucket, prefix=prefix)

    for blob in blobs:
        parts = blob.name.split("/")
        if len(parts) >= 2:
            shops.add(parts[1])

    return jsonify({
        "ofm": ofm,
        "shops": sorted(list(shops))
    })

#-------------------โหลด mode ของแผงที่เลือก --------------------
@app.route("/get_modes", methods=["GET"])
def get_modes():
    """
    ?ofm=ตลาดสดมารวย&shop=แผงผักดารุณี
    """
    ofm = request.args.get("ofm")
    shop = request.args.get("shop")

    if not ofm or not shop:
        return jsonify({"error": "missing params"}), 400

    prefix = f"{ofm}/{shop}/"
    modes = set()

    blobs = storage_client.list_blobs(bucket, prefix=prefix)

    for blob in blobs:
        parts = blob.name.split("/")
        if len(parts) >= 3:
            modes.add(parts[2])

    return jsonify({
        "shop": shop,
        "modes": sorted(list(modes))
    })

#-------------------อ่านจาก path storege firebase โหลดรูปแบบ Pagination-----------
@app.route("/get_images", methods=["GET"])
def get_images():
    """
    ?ofm=ตลาดสดมารวย
    &shop=แผงผักดารุณี
    &mode=ผืชหัว
    &page=1
    &page_size=20
    """

    ofm = request.args.get("ofm")
    shop = request.args.get("shop")
    mode = request.args.get("mode")

    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))

    if not ofm or not shop or not mode:
        return jsonify({"error": "missing params"}), 400

    prefix = f"{ofm}/{shop}/{mode}/"
    blobs = storage_client.list_blobs(bucket, prefix=prefix)

    image_urls = []

    for blob in blobs:
        if blob.name.lower().endswith(".jpg"):
            image_urls.append(
                f"https://storage.googleapis.com/{bucket.name}/{blob.name}"
            )

    total = len(image_urls)
    start = (page - 1) * page_size
    end = start + page_size

    return jsonify({
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": end < total,
        "images": image_urls[start:end]
    })

 
#----------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
